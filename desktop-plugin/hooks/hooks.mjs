// Native adapter: Python owns mode state; every call uses the current host ID.
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

function contextText(output) {
  const specific = output.hookSpecificOutput;
  return record(specific) && typeof specific.additionalContext === 'string'
    ? specific.additionalContext : '';
}

function logMessage($, output) {
  if (typeof output.systemMessage === 'string' && output.systemMessage) $.ui.log(output.systemMessage);
}

function withContext(result, contexts) {
  const extra = contexts.filter(Boolean);
  if (!extra.length || result.deny) return result;
  return { ...result, context: [...(result.context || []), ...extra] };
}

async function current($, event = {}) {
  return { session_id: await $.session.id(), cwd: event.cwd || await $.session.cwd() };
}

async function ensureStart($, base, starts, pendingContext, source = 'startup') {
  if (!starts.has(base.session_id)) {
    const start = runHook($, { ...base, hook_event_name: 'SessionStart', source })
      .then(out => {
        logMessage($, out);
        pendingContext.set(base.session_id, contextText(out));
      })
      .catch(error => {
        starts.delete(base.session_id); // retry a transient bridge failure
        throw error;
      });
    starts.set(base.session_id, start);
  }
  await starts.get(base.session_id);
}

export function register(on) {
  // Registration-local, keyed by host session: /clear and resume may rebind the
  // ID without reloading the module or raising session.start again.
  const starts = new Map();
  const pendingContext = new Map();

  function takeContext(session) {
    const text = pendingContext.get(session) || '';
    pendingContext.delete(session);
    return text;
  }

  on('session.start', async ($, event, next) => {
    try {
      await ensureStart($, await current($, event), starts, pendingContext, event.source || 'startup');
    } catch (error) {
      $.ui.log(FAILURE);
    }
    return next(event);
  });

  on('prompt.submit', async ($, event, next) => {
    // Only actual human submissions can choose a mode or begin a new task.
    if (event.isMeta || event.origin?.kind !== 'human') return next(event);
    const contexts = [];
    try {
      const base = await current($);
      await ensureStart($, base, starts, pendingContext);
      contexts.push(takeContext(base.session_id));
      const out = await runHook($, {
        ...base, hook_event_name: 'UserPromptSubmit', prompt: event.text,
        origin: event.origin,
      });
      logMessage($, out);
      contexts.push(contextText(out));
    } catch (error) {
      contexts.push(FAILURE);
    }
    // The prompt has already entered the model when next() resolves. Attach
    // context on the way DOWN; do not rewrite the human text or a downstream drop.
    return next({ ...event, context: [...(event.context || []), ...contexts.filter(Boolean)] });
  });

  on('tool.call', async ($, event, next) => {
    const { tool, tool_use_id, agentId, ...toolInput } = event;
    const contexts = [];
    let base;
    try {
      base = await current($);
      await ensureStart($, base, starts, pendingContext);
      contexts.push(takeContext(base.session_id));
      const out = await runHook($, {
        ...base, hook_event_name: 'PreToolUse', tool_name: tool,
        tool_use_id, tool_input: toolInput,
      });
      logMessage($, out);
      contexts.push(contextText(out));
      const specific = out.hookSpecificOutput;
      if (record(specific) && specific.permissionDecision === 'deny') {
        return { deny: specific.permissionDecisionReason || 'CCL refused this tool call' };
      }
    } catch (error) {
      return { deny: FAILURE };
    }
    const result = await next(event);
    // Native tool.call surrounds execution; there is no classic.PostToolUse
    // event here. Preserve the real answer, then record only the matching menu.
    if (tool === 'AskUserQuestion' && !result.deny && !result.isError) {
      try {
        const out = await runHook($, {
          ...base, hook_event_name: 'PostToolUse', tool_name: tool,
          tool_input: toolInput, tool_response: result.result,
        });
        logMessage($, out);
        contexts.push(contextText(out));
      } catch (error) {
        contexts.push('CCL 未能记录模式回答；请对当前 session 执行 ccl mode sync，不要重复询问用户。');
      }
    }
    return withContext(result, contexts);
  });

  on('turn.complete', async ($, event, next) => {
    // A child agent ending must not close/report the parent's mode task.
    if (event.agentId) return next(event);
    try {
      const out = await runHook($, {
        ...await current($), hook_event_name: 'Stop', stop_hook_active: false,
        last_assistant_message: event.text || '',
      });
      logMessage($, out);
      if (out.decision === 'block' && out.reason) $.ui.log(out.reason);
    } catch (error) {
      // The turn has already finished; never start another model turn here.
    }
    return next(event);
  });
}
