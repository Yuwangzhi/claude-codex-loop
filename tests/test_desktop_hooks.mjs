import assert from 'node:assert/strict';
import test from 'node:test';
import { register } from '../desktop-plugin/hooks/hooks.mjs';

function harness(output = {}, downstream = {}) {
  const handlers = new Map();
  register((name, hook) => {
    const entry = { hook };
    handlers.set(name, entry);
    return { catch: (handler) => { entry.fail = handler; } };
  });
  const calls = [];
  const logs = [];
  const api = {
    plugin: { root: '/plugins/ccl with spaces' },
    fs: { read: async () => JSON.stringify({ python: '/Python Path/python', ccl: '/CCL Path/ccl.py' }) },
    process: { run: async (...args) => { calls.push(args); return { exitCode: 0, stdout: JSON.stringify(output) }; } },
    session: { id: async () => 'session-123', cwd: async () => '/project with spaces' },
    ui: { log: (text) => logs.push(text) },
  };
  let nextCalls = 0;
  const next = async () => { nextCalls += 1; return downstream; };
  return { handlers, api, calls, logs, next, nextCalls: () => nextCalls };
}

test('registers all five lifecycle events with failure handlers', () => {
  const h = harness();
  assert.deepEqual([...h.handlers.keys()], [
    'classic.SessionStart', 'classic.UserPromptSubmit', 'classic.PostToolUse', 'classic.PreToolUse', 'classic.Stop',
  ]);
  for (const handler of h.handlers.values()) assert.equal(typeof handler.fail, 'function');
});

test('prompt payload goes only to stdin and contexts from other hooks survive', async () => {
  const h = harness({ hookSpecificOutput: { additionalContext: 'CCL context' } }, { additionalContext: ['other context'], block: 'other block' });
  const event = { hook_event_name: 'UserPromptSubmit', session_id: 's', cwd: '/project', prompt: '1; $(touch unsafe)' };
  const result = await h.handlers.get('classic.UserPromptSubmit').hook(h.api, event, h.next);
  assert.deepEqual(h.calls[0][0], ['/Python Path/python', '/CCL Path/ccl.py', 'hook']);
  assert.deepEqual(JSON.parse(h.calls[0][1].stdin), { ...event, ccl_transport: 'module' });
  assert.equal(h.calls[0][1].timeoutMs, 8000);
  assert.deepEqual(result.additionalContext, ['other context', 'CCL context']);
  assert.equal(result.block, 'other block');
  assert.equal(h.nextCalls(), 1);
  assert.deepEqual(h.logs, []);
});

test('converts the mod PreToolUse envelope and retains both denials', async () => {
  const h = harness({ hookSpecificOutput: { permissionDecision: 'deny', permissionDecisionReason: 'select mode' } }, { deny: 'policy denial', additionalContext: ['policy context'] });
  const result = await h.handlers.get('classic.PreToolUse').hook(h.api, { tool: 'Bash', tool_use_id: 't1', command: 'echo ok' }, h.next);
  assert.deepEqual(JSON.parse(h.calls[0][1].stdin), {
    hook_event_name: 'PreToolUse', session_id: 'session-123', cwd: '/project with spaces',
    tool_name: 'Bash', tool_use_id: 't1', tool_input: { command: 'echo ok' }, ccl_transport: 'module',
  });
  assert.equal(result.deny, 'policy denial\nselect mode');
  assert.deepEqual(result.additionalContext, ['policy context']);
  assert.equal(h.nextCalls(), 1);
});

test('CCL denial removes an allow; CCL pass preserves a later ask', async () => {
  const h = harness({ hookSpecificOutput: { permissionDecision: 'deny', permissionDecisionReason: 'mode' } }, { allow: true });
  const entry = h.handlers.get('classic.PreToolUse');
  assert.deepEqual(await entry.hook(h.api, { tool: 'Read', file_path: '/x' }, h.next), { deny: 'mode' });
  const pass = harness({}, { ask: 'user consent needed' });
  assert.deepEqual(await pass.handlers.get('classic.PreToolUse').hook(pass.api, { tool: 'Read', file_path: '/x' }, pass.next), { ask: 'user consent needed' });
});

test('process failure and malformed/truncated output fail closed at PreToolUse', async () => {
  for (const result of [{ exitCode: 1, stdout: '{}' }, { exitCode: 0, stdout: 'not json' }, { exitCode: 0, stdout: '[]' }, { exitCode: 0, stdout: '{}', isStdoutTruncated: true }]) {
    const h = harness();
    h.api.process.run = async () => result;
    const entry = h.handlers.get('classic.PreToolUse');
    await assert.rejects(entry.hook(h.api, { tool: 'Bash', command: 'echo ok' }, h.next));
    assert.equal(typeof entry.fail().deny, 'string');
    assert.equal(h.nextCalls(), 0);
  }
  const h = harness();
  h.api.process.run = async () => { throw new Error('timeout'); };
  await assert.rejects(h.handlers.get('classic.PreToolUse').hook(h.api, { tool: 'Bash' }, h.next));
  assert.equal(h.nextCalls(), 0);
});

test('Stop keeps all blocking and continuation decisions', async () => {
  const h = harness({ decision: 'block', reason: 'CCL still active' }, { block: 'other still active', preventContinuation: true, stopReason: 'other stopped' });
  const result = await h.handlers.get('classic.Stop').hook(h.api, { hook_event_name: 'Stop', cwd: '/project', session_id: 's' }, h.next);
  assert.deepEqual(result, { block: 'other still active\nCCL still active', preventContinuation: true, stopReason: 'other stopped' });
});

test('only question answers use the PostToolUse process bridge', async () => {
  const h = harness();
  const entry = h.handlers.get('classic.PostToolUse');
  await entry.hook(h.api, { tool_name: 'Read' }, h.next);
  assert.equal(h.calls.length, 0);
  await entry.hook(h.api, { hook_event_name: 'PostToolUse', tool_name: 'AskUserQuestion' }, h.next);
  assert.equal(h.calls.length, 1);
  assert.equal(h.nextCalls(), 2);
});

test('Stop failure blocks once and prompt failure retains downstream context', async () => {
  const h = harness({}, { additionalContext: ['other'] });
  const entry = h.handlers.get('classic.Stop');
  assert.equal(typeof (await entry.fail(h.api, { stop_hook_active: false }, h.next)).block, 'string');
  assert.deepEqual(await entry.fail(h.api, { stop_hook_active: true }, h.next), { additionalContext: ['other'] });
  const prompt = await h.handlers.get('classic.UserPromptSubmit').fail(h.api, {}, h.next);
  assert.equal(prompt.additionalContext[0], 'other');
  assert.equal(prompt.additionalContext.length, 2);
});
