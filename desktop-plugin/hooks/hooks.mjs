// Claude Code native hook adapter. Python remains the single mode-state owner.
// Uses the native event API (session.start / prompt.submit / tool.call /
// turn.complete). SessionStart context is folded into the first prompt, because
// the built-in sec-default plugin claims prompt.context before CCL can see it.
const FAILURE = 'CCL 模式检查失败，已暂停本次工具执行；请检查 CCL 安装后重试。';
let sessionContext = '';
let sessionStarted = false;

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

function contextText(output) {
  const specific = output.hookSpecificOutput;
  if (record(specific) && typeof specific.additionalContext === 'string' && specific.additionalContext) {
    return specific.additionalContext;
  }
  return '';
}

function logMessage($, output) {
  if (typeof output.systemMessage === 'string' && output.systemMessage) $.ui.log(output.systemMessage);
}

async function sessionStart($, event, next) {
  try {
    const out = await runHook($, {
      hook_event_name: 'SessionStart',
      session_id: await $.session.id(),
      cwd: event.cwd || await $.session.cwd(),
      source: event.source || 'startup',
    });
    logMessage($, out);
    sessionContext = contextText(out);
    sessionStarted = true;
  } catch (err) {
    sessionContext = FAILURE;
  }
  return next(event);
}

async function promptSubmit($, event, next) {
  let extra = '';
  if (!sessionStarted) {
    try {
      const start = await runHook($, {
        hook_event_name: 'SessionStart',
        session_id: await $.session.id(),
        cwd: await $.session.cwd(),
        source: 'startup',
      });
      logMessage($, start);
      extra += contextText(start);
      sessionStarted = true;
    } catch (err) {
      extra += FAILURE;
    }
  }
  if (sessionContext) {
    extra += (extra ? '\n\n' : '') + sessionContext;
    sessionContext = '';
  }
  try {
    const out = await runHook($, {
      hook_event_name: 'UserPromptSubmit',
      session_id: await $.session.id(),
      cwd: await $.session.cwd(),
      prompt: event.text || '',
    });
    logMessage($, out);
    const context = contextText(out);
    if (context) extra += (extra ? '\n\n' : '') + context;
  } catch (err) {
    extra += (extra ? '\n\n' : '') + FAILURE;
  }
  const downstream = await next(event);
  if (!extra) return downstream;
  const text = record(downstream) && typeof downstream.text === 'string' ? downstream.text : (event.text || '');
  return { ...(record(downstream) ? downstream : {}), text: text + '\n\n' + extra };
}

async function toolCall($, event, next) {
  const { tool, tool_use_id, ...toolInput } = event;
  try {
    const out = await runHook($, {
      hook_event_name: 'PreToolUse',
      session_id: await $.session.id(),
      cwd: await $.session.cwd(),
      tool_name: tool,
      tool_use_id,
      tool_input: toolInput,
    });
    logMessage($, out);
    const specific = out.hookSpecificOutput;
    if (record(specific) && specific.permissionDecision === 'deny') {
      return { deny: specific.permissionDecisionReason || 'CCL refused this tool call' };
    }
  } catch (err) {
    return { deny: FAILURE };
  }
  return next(event);
}

async function turnComplete($, event, next) {
  try {
    const out = await runHook($, {
      hook_event_name: 'Stop',
      session_id: await $.session.id(),
      cwd: await $.session.cwd(),
      stop_hook_active: false,
    });
    logMessage($, out);
  } catch (err) {
    // The turn has already finished; never turn a reporting failure into a loop.
  }
  return next(event);
}

export function register(on) {
  on('session.start', sessionStart);
  on('prompt.submit', promptSubmit);
  on('tool.call', toolCall);
  on('turn.complete', turnComplete);
}
