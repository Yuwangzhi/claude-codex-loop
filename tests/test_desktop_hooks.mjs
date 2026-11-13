import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { register } from '../desktop-plugin/hooks/hooks.mjs';

function harness(output = {}, downstream = { result: {} }) {
  const handlers = new Map();
  register((name, hook) => handlers.set(name, hook));
  const calls = [], logs = [], entered = [];
  let session = 'session-123';
  const api = {
    plugin: { root: '/plugins/ccl with spaces' },
    fs: { read: async () => JSON.stringify({ python: '/Python Path/python', ccl: '/CCL Path/ccl.py' }) },
    process: { run: async (...args) => {
      calls.push(args);
      const response = typeof output === 'function' ? output(JSON.parse(args[1].stdin)) : output;
      return { exitCode: 0, stdout: JSON.stringify(response) };
    } },
    session: { id: async () => session, cwd: async () => '/project with spaces' },
    ui: { log: text => logs.push(text) },
  };
  const next = async event => { entered.push(event); return downstream; };
  return { handlers, api, calls, logs, entered, next, setSession: value => { session = value; } };
}
const human = text => ({ text, origin: { kind: 'human' }, wait: false });
const payloads = h => h.calls.map(call => JSON.parse(call[1].stdin));

test('registers the current native lifecycle events', () => {
  assert.deepEqual([...harness().handlers.keys()], ['session.start', 'prompt.submit', 'tool.call', 'turn.complete']);
});

test('attaches contexts before next enters the prompt, without changing human text', async () => {
  const h = harness(p => ({ hookSpecificOutput: { additionalContext: p.hook_event_name } }), { text: 'accepted' });
  const event = { ...human('2; $(touch unsafe)'), context: ['other context'] };
  const result = await h.handlers.get('prompt.submit')(h.api, event, h.next);
  assert.deepEqual(h.calls[0][0], ['/Python Path/python', '/CCL Path/ccl.py', 'hook']);
  assert.equal(h.calls[0][1].timeoutMs, 8000);
  assert.equal(payloads(h)[1].prompt, event.text);
  assert.equal(payloads(h)[1].ccl_transport, 'module');
  assert.deepEqual(h.entered[0], { ...event, context: ['other context', 'SessionStart', 'UserPromptSubmit'] });
  assert.deepEqual(result, { text: 'accepted' });
});

test('preserves a downstream prompt drop and never invents accepted text', async () => {
  const h = harness({}, { drop: 'policy declined' });
  assert.deepEqual(await h.handlers.get('prompt.submit')(h.api, human('2'), h.next), { drop: 'policy declined' });
});

test('non-human and meta prompts do not change the mode', async () => {
  for (const event of [{ text: '模式一', origin: { kind: 'plugin' } }, { ...human('模式一'), isMeta: true }]) {
    const h = harness();
    await h.handlers.get('prompt.submit')(h.api, event, h.next);
    assert.equal(h.calls.length, 0);
    assert.deepEqual(h.entered, [event]);
  }
});

test('starts once per registration and detects an ID change without session.start', async () => {
  const h = harness();
  await h.handlers.get('session.start')(h.api, {}, h.next);
  await h.handlers.get('prompt.submit')(h.api, human('2'), h.next);
  h.setSession('resumed-session');
  await h.handlers.get('tool.call')(h.api, { tool: 'Read', file_path: '/x' }, h.next);
  assert.deepEqual(payloads(h).map(p => [p.hook_event_name, p.session_id]), [
    ['SessionStart', 'session-123'], ['UserPromptSubmit', 'session-123'],
    ['SessionStart', 'resumed-session'], ['PreToolUse', 'resumed-session'],
  ]);
  const other = harness();
  await other.handlers.get('prompt.submit')(other.api, human('1'), other.next);
  assert.equal(payloads(other)[0].hook_event_name, 'SessionStart');
});

test('concurrent tool calls share initialization but not their payloads', async () => {
  const h = harness();
  await Promise.all(['a', 'b'].map(file_path => h.handlers.get('tool.call')(h.api, { tool: 'Read', file_path }, h.next)));
  assert.equal(payloads(h).filter(p => p.hook_event_name === 'SessionStart').length, 1);
  assert.equal(payloads(h).filter(p => p.hook_event_name === 'PreToolUse').length, 2);
});

test('denial prevents execution; passes retain downstream results and context', async () => {
  const h = harness(p => p.hook_event_name === 'PreToolUse'
    ? { hookSpecificOutput: { permissionDecision: 'deny', permissionDecisionReason: 'select mode' } } : {});
  assert.deepEqual(await h.handlers.get('tool.call')(h.api, { tool: 'Bash', tool_use_id: 't1', command: 'true' }, h.next), { deny: 'select mode' });
  assert.equal(h.entered.length, 0);
  assert.deepEqual(payloads(h)[1].tool_input, { command: 'true' });
  const pass = harness(p => p.hook_event_name === 'PreToolUse'
    ? { hookSpecificOutput: { additionalContext: 'recovered current session' } } : {},
  { result: { value: 1 }, ref: 7, context: ['policy context'] });
  assert.deepEqual(await pass.handlers.get('tool.call')(pass.api, { tool: 'Read', file_path: '/x' }, pass.next),
    { result: { value: 1 }, ref: 7, context: ['policy context', 'recovered current session'] });
  const denied = harness({}, { deny: 'downstream permission refusal' });
  assert.deepEqual(await denied.handlers.get('tool.call')(denied.api, { tool: 'Read' }, denied.next), { deny: 'downstream permission refusal' });
});

test('process failures fail closed, and a transient initialization failure can retry', async () => {
  for (const result of [{ exitCode: 1, stdout: '{}' }, { exitCode: 0, stdout: 'not json' },
    { exitCode: 0, stdout: '[]' }, { exitCode: 0, stdout: '{}', isStdoutTruncated: true }]) {
    const h = harness();
    h.api.process.run = async () => result;
    assert.equal(typeof (await h.handlers.get('tool.call')(h.api, { tool: 'Read' }, h.next)).deny, 'string');
    assert.equal(h.entered.length, 0);
    h.api.process.run = async () => ({ exitCode: 0, stdout: '{}' });
    assert.deepEqual(await h.handlers.get('tool.call')(h.api, { tool: 'Read' }, h.next), { result: {} });
  }
});

test('records an actual AskUserQuestion result after execution, never a denial or error', async () => {
  const answer = { answers: { '模式1还是模式2？': '模式二：仅 Claude' } };
  const h = harness({}, { result: answer, ref: 9 });
  const input = { tool: 'AskUserQuestion', questions: [{ question: '模式1还是模式2？' }] };
  await h.handlers.get('tool.call')(h.api, input, h.next);
  const post = payloads(h).at(-1);
  assert.equal(post.hook_event_name, 'PostToolUse');
  assert.deepEqual(post.tool_response, answer);
  assert.deepEqual(post.tool_input, { questions: input.questions });
  for (const downstream of [{ deny: 'no' }, { isError: true, result: 'cancelled' }]) {
    const failed = harness({}, downstream);
    await failed.handlers.get('tool.call')(failed.api, input, failed.next);
    assert.equal(payloads(failed).some(p => p.hook_event_name === 'PostToolUse'), false);
  }
});

test('turn completion reports the actual final text without spawning a turn', async () => {
  const h = harness({ decision: 'block', reason: 'finish your mode task' }, { text: 'done' });
  assert.deepEqual(await h.handlers.get('turn.complete')(h.api, { text: 'done' }, h.next), { text: 'done' });
  assert.equal(payloads(h)[0].last_assistant_message, 'done');
  assert.deepEqual(h.logs, ['finish your mode task']);
  const child = harness();
  await child.handlers.get('turn.complete')(child.api, { text: 'child done', agentId: 'agent' }, child.next);
  assert.equal(child.calls.length, 0);
});

test('real Python bridge: a resumed session recovers its choice without another prompt', async () => {
  const root = mkdtempSync(join(tmpdir(), 'ccl-native-recovery-'));
  try {
    const script = fileURLToPath(new URL('../ccl.py', import.meta.url));
    const state = join(root, 'state');
    const h = harness({}, { result: { ok: true } });
    h.api.session.cwd = async () => root;
    h.api.process.run = async (argv, options) => {
      const result = spawnSync(process.env.PYTHON || 'python3', [script, 'hook'], {
        encoding: 'utf8', input: options.stdin, cwd: root,
        env: { ...process.env, CCL_STATE_DIR: state, CCL_CONFIG_DIR: join(root, 'config'), CLAUDE_CONFIG_DIR: join(root, 'claude') },
      });
      assert.equal(result.status, 0, result.stderr);
      return { exitCode: result.status, stdout: result.stdout };
    };
    await h.handlers.get('prompt.submit')(h.api, human('2，然后修复问题'), h.next);
    h.setSession('resumed-without-prompt');
    const result = await h.handlers.get('tool.call')(h.api, { tool: 'Read', file_path: '/x' }, h.next);
    assert.equal(result.deny, undefined);
    assert.ok(result.context.some(text => text.includes('resumed-without-prompt')));
    const saved = JSON.parse(readFileSync(join(state, 'sessions/resumed-without-prompt.json'), 'utf8'));
    assert.equal(saved.mode, '2');
    assert.equal(saved.selection_source, 'default_last_mode');
    const denied = await h.handlers.get('tool.call')(h.api, { tool: 'Bash', command: 'codex exec test' }, h.next);
    assert.match(denied.deny, /模式二/);
    writeFileSync(join(state, 'mode-preference.json'), JSON.stringify({ mode: '1' }));
    await h.handlers.get('tool.call')(h.api, { tool: 'Read', file_path: '/x' }, h.next);
    assert.equal(JSON.parse(readFileSync(join(state, 'sessions/resumed-without-prompt.json'), 'utf8')).mode, '2');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
