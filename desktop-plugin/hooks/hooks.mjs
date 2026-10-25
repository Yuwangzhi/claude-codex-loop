// Claude Code 2.1.308+ mod bridge. Python remains the single mode-state owner.
// No shell and no transcript/prompt logging: the original payload travels on stdin.
const FAILURE = 'CCL 模式检查失败，已暂停本次工具执行；请检查 CCL 安装后重试。';

function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

async function runHook($, payload) {
  const config = JSON.parse(await $.fs.read($.plugin.root + '/bridge.json'));
  if (!record(config) || typeof config.python !== 'string' || !config.python ||
      typeof config.ccl !== 'string' || !config.ccl) {
    throw new Error('CCL bridge configuration is invalid');
  }
  const result = await $.process.run([config.python, config.ccl, 'hook'], {
    cwd: payload.cwd,
    stdin: JSON.stringify({ ...payload, ccl_transport: 'module' }),
    timeoutMs: 8000,
  });
  if (result.exitCode !== 0 || result.isStdoutTruncated) {
    throw new Error('CCL hook process did not complete');
  }
  const output = JSON.parse(result.stdout);
  if (!record(output)) throw new Error('CCL hook returned invalid JSON');
  return output;
}

function classicResult(output) {
  const result = {};
  const specific = output.hookSpecificOutput;
  if (record(specific) && typeof specific.additionalContext === 'string' && specific.additionalContext) {
    result.additionalContext = [specific.additionalContext];
  }
  if (output.decision === 'block') result.block = output.reason || 'CCL requires attention';
  if (output.continue === false) {
    result.preventContinuation = true;
    if (typeof output.stopReason === 'string') result.stopReason = output.stopReason;
  }
  return result;
}

function mergeClassic(downstream, own) {
  const result = { ...downstream, ...own };
  if (downstream.additionalContext || own.additionalContext) {
    result.additionalContext = [...(downstream.additionalContext || []), ...(own.additionalContext || [])];
  }
  if (downstream.block && own.block) result.block = downstream.block + '\n' + own.block;
  if (downstream.preventContinuation) {
    result.preventContinuation = true;
    if (downstream.stopReason) result.stopReason = downstream.stopReason;
  }
  return result;
}

async function classic($, event, next) {
  const output = await runHook($, event);
  const downstream = await next(event);
  // A command hook's systemMessage is user-facing rather than model context.
  if (typeof output.systemMessage === 'string' && output.systemMessage) $.ui.log(output.systemMessage);
  return mergeClassic(downstream, classicResult(output));
}

async function preTool($, event, next) {
  const { tool, tool_use_id, agentId, ...toolInput } = event;
  const output = await runHook($, {
    hook_event_name: 'PreToolUse',
    session_id: await $.session.id(),
    cwd: await $.session.cwd(),
    tool_name: tool,
    tool_use_id,
    tool_input: toolInput,
    ...(agentId ? { agent_id: agentId } : {}),
  });
  const downstream = await next(event);
  const specific = output.hookSpecificOutput;
  if (record(specific) && specific.permissionDecision === 'deny') {
    // Never turn a later denial/ask into an allow, and keep later context.
    const { allow, ask, deny, ...rest } = downstream;
    const reason = specific.permissionDecisionReason || 'CCL refused this tool call';
    return { ...rest, deny: deny ? deny + '\n' + reason : reason };
  }
  return downstream;
}

async function contextFailure($, event, next) {
  const downstream = await next(event);
  return mergeClassic(downstream, { additionalContext: [FAILURE] });
}

export function register(on) {
  on('classic.SessionStart', classic).catch(contextFailure);
  on('classic.UserPromptSubmit', classic).catch(contextFailure);
  on('classic.PostToolUse', async ($, event, next) => {
    if (event.tool_name !== 'AskUserQuestion') return next(event);
    return classic($, event, next);
  }).catch(contextFailure);
  on('classic.PreToolUse', preTool).catch(() => ({ deny: FAILURE }));
  on('classic.Stop', classic).catch(async ($, event, next) => {
    const downstream = await next(event);
    if (event.stop_hook_active) return downstream;
    return mergeClassic(downstream, { block: FAILURE });
  });
}
