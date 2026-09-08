import assert from 'node:assert/strict';
import { readFile, rename } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';

const BOOTSTRAP_SKILL_DIR = 'using-graph-works'; // <- P2's value

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '../..');
const packageJsonPath = resolve(repoRoot, 'package.json');
const extensionPath = resolve(repoRoot, '.pi/extensions/graph-works.ts');
const bootstrapSkillPath = resolve(repoRoot, 'skills', BOOTSTRAP_SKILL_DIR, 'SKILL.md');

async function readPackageJson() {
  return JSON.parse(await readFile(packageJsonPath, 'utf8'));
}

async function loadExtension() {
  const handlers = new Map();
  const pi = {
    on(event, handler) {
      if (!handlers.has(event)) handlers.set(event, []);
      handlers.get(event).push(handler);
    },
  };
  const mod = await import(
    pathToFileURL(extensionPath).href + `?cachebust=${Date.now()}-${Math.random()}`
  );
  mod.default(pi);
  return { handlers };
}

function firstHandler(handlers, event) {
  const eventHandlers = handlers.get(event) ?? [];
  assert.equal(eventHandlers.length, 1, `expected one ${event} handler`);
  return eventHandlers[0];
}

function textOf(message) {
  if (typeof message.content === 'string') return message.content;
  return message.content
    .filter((part) => part.type === 'text')
    .map((part) => part.text)
    .join('\n');
}

test('package.json declares a pi package with skills and extension resources', async () => {
  const pkg = await readPackageJson();

  assert.equal(pkg.name, 'gw');
  assert.equal(pkg.version, '0.1.0');
  assert.equal(pkg.main, undefined, 'package.json must not declare main');
  assert.ok(pkg.keywords.includes('pi-package'));
  assert.deepEqual(pkg.pi.skills, ['./skills']);
  assert.deepEqual(pkg.pi.extensions, ['./.pi/extensions/graph-works.ts']);
});

test('the extension injects the same skill the Claude Code session-start hook injects', async () => {
  const hook = await readFile(resolve(repoRoot, 'hooks/session-start'), 'utf8');
  const match = hook.match(/skills\/([a-z0-9-]+)\/SKILL\.md/);
  assert.ok(match, 'session-start hook should name a bootstrap skill');
  assert.equal(
    match[1],
    BOOTSTRAP_SKILL_DIR,
    'one bootstrap source, two harnesses: Pi and Claude Code must agree',
  );

  const extension = await readFile(extensionPath, 'utf8');
  assert.match(extension, new RegExp(`"${BOOTSTRAP_SKILL_DIR}"`));
});

test('extension registers lifecycle hooks without pre-compaction injection', async () => {
  const { handlers } = await loadExtension();

  for (const event of [
    'resources_discover',
    'session_start',
    'session_compact',
    'context',
    'agent_end',
  ]) {
    assert.equal((handlers.get(event) ?? []).length, 1, `missing ${event} handler`);
  }
  assert.equal((handlers.get('session_before_compact') ?? []).length, 0);
});

test('resources_discover contributes the bundled skills directory', async () => {
  const { handlers } = await loadExtension();
  const discover = firstHandler(handlers, 'resources_discover');

  const result = await discover({ type: 'resources_discover', cwd: repoRoot, reason: 'startup' }, {});

  assert.deepEqual(result.skillPaths, [resolve(repoRoot, 'skills')]);
});

test('startup context injects the bootstrap as one user message until agent_end', async () => {
  const { handlers } = await loadExtension();
  const sessionStart = firstHandler(handlers, 'session_start');
  const context = firstHandler(handlers, 'context');
  const agentEnd = firstHandler(handlers, 'agent_end');

  await sessionStart({ type: 'session_start', reason: 'startup' }, {});

  const originalMessages = [
    { role: 'user', content: [{ type: 'text', text: 'Let us make a react todo list' }], timestamp: 1 },
  ];
  const result = await context({ type: 'context', messages: originalMessages }, {});

  assert.equal(result.messages.length, 2);
  assert.equal(result.messages[0].role, 'user');
  assert.match(textOf(result.messages[0]), /You have graph-works workflows/);
  assert.match(textOf(result.messages[0]), /Pi tool mapping/);
  assert.doesNotMatch(textOf(result.messages[0]), /superpowers/i);
  assert.equal(result.messages[1], originalMessages[0]);

  const repeated = await context({ type: 'context', messages: originalMessages }, {});
  assert.equal(repeated.messages.length, 2);

  const alreadyInjected = await context({ type: 'context', messages: result.messages }, {});
  assert.equal(alreadyInjected, undefined, 'bootstrap should not duplicate when already present');

  await agentEnd({ type: 'agent_end', messages: [] }, {});
  const afterEnd = await context({ type: 'context', messages: originalMessages }, {});
  assert.equal(afterEnd, undefined, 'startup bootstrap should clear after agent_end');
});

test('session_compact injects bootstrap after compaction summaries, not before compaction', async () => {
  const { handlers } = await loadExtension();
  const sessionCompact = firstHandler(handlers, 'session_compact');
  const context = firstHandler(handlers, 'context');

  await sessionCompact({ type: 'session_compact', compactionEntry: {}, fromExtension: false }, {});

  const summary = { role: 'compactionSummary', summary: 'Prior work summary', tokensBefore: 123, timestamp: 1 };
  const user = { role: 'user', content: [{ type: 'text', text: 'Continue' }], timestamp: 2 };
  const result = await context({ type: 'context', messages: [summary, user] }, {});

  assert.equal(result.messages.length, 3);
  assert.equal(result.messages[0], summary);
  assert.equal(result.messages[1].role, 'user');
  assert.match(textOf(result.messages[1]), /You have graph-works workflows/);
  assert.equal(result.messages[2], user);
});

test('context skips bootstrap injection when the bundled skill cannot be read', async () => {
  const movedAsidePath = `${bootstrapSkillPath}.moved-for-test`;
  await rename(bootstrapSkillPath, movedAsidePath);
  try {
    const { handlers } = await loadExtension();
    const sessionStart = firstHandler(handlers, 'session_start');
    const context = firstHandler(handlers, 'context');

    await sessionStart({ type: 'session_start', reason: 'startup' }, {});

    const originalMessages = [
      { role: 'user', content: [{ type: 'text', text: 'Let us make a react todo list' }], timestamp: 1 },
    ];
    const result = await context({ type: 'context', messages: originalMessages }, {});

    assert.equal(result, undefined, 'no bootstrap message should be injected when the skill file is unreadable');
  } finally {
    await rename(movedAsidePath, bootstrapSkillPath);
  }
});
