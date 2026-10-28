"""Small, explicit context manifests; no embeddings, private chat import or writes."""
import json
import re
import governance


def terms(text):
    words = set(re.findall(r'[a-z0-9_./-]{3,}', text.lower()))
    for phrase in re.findall(r'[\u4e00-\u9fff]+', text):
        words.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
    return words


def build(project, config, spec=None, max_chars=20000):
    spec = spec or {}
    if type(max_chars) is not int or not 1000 <= max_chars <= 200000:
        raise ValueError('Context budget must be 1000..200000 characters')
    query = terms(' '.join(str(spec.get(k, '')) for k in ('title', 'goal', 'plan')))
    required = spec.get('context_files', [])
    candidates, omitted, warnings = [], [], []
    indexed, seen = {}, set()
    index_path = project / '.ai-collab/context.json'
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding='utf-8-sig'))
        if not isinstance(index, dict) or index.get('version') != 1 or not isinstance(index.get('entries'), list):
            raise ValueError('Invalid .ai-collab/context.json index')
        for entry in index['entries']:
            if (not isinstance(entry, dict) or not isinstance(entry.get('read_when'), str) or
                    not isinstance(entry.get('source_hashes', {}), dict) or
                    not all(isinstance(k, str) and isinstance(v, str) for k, v in entry.get('source_hashes', {}).items())):
                raise ValueError('Invalid context index entry')
            path = governance.local_file(project, entry.get('path'))
            if path.suffix.lower() != '.md' or path in indexed:
                raise ValueError('Indexed memory must be a unique Markdown file')
            indexed[path] = entry

    def add(path, required=False, hint='', covers=None, sections=False):
        path = path.resolve()
        if path in seen:
            return
        seen.add(path)
        # Freshness follows the file identity, including legacy/explicit aliases.
        if path in indexed:
            covers = indexed[path].get('source_hashes', {})
            hint = indexed[path]['read_when']
        label = str(path)
        if not path.is_file():
            if required:
                raise ValueError('Required context missing: ' + label)
            omitted.append({'path': label, 'reason': 'missing'})
            return
        body = path.read_text(encoding='utf-8-sig')
        sha = governance.file_digest(path)
        stale = []
        for name, expected in (covers or {}).items():
            actual = governance.file_digest(governance.local_file(project, name))
            if actual != expected:
                stale.append(name)
        if stale:
            if required:
                raise ValueError('Required context is stale: ' + label)
            omitted.append({'path': label, 'sha256': sha, 'reason': 'stale', 'changed_sources': stale})
            return
        chunks = re.split(r'(?m)(?=^#{1,6} )', body) if sections else [body]
        for index, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            score = len(query & terms(hint + ' ' + chunk))
            candidates.append({'path': label, 'section': index, 'sha256': sha,
                               'read_when': hint, 'source_hashes': covers or {}, 'required': required,
                               'relevance': score, 'content': chunk})

    for name in required:
        add(governance.local_file(project, name), required=True, hint='Explicit task context')
    for path in (config / 'SHARED_MEMORY.md', project / '.ai-collab/MEMORY.md'):
        add(path, sections=True, hint='Shared context, not task authority')
    for path in indexed:
        add(path)
    candidates.sort(key=lambda x: (not x['required'], -x['relevance']))
    selected, used = [], 0
    for item in candidates:
        size = len(item['content'])
        if used + size > max_chars:
            if item['required']:
                raise ValueError('Required context exceeds context_chars; raise the budget or narrow context_files')
            omitted.append({k: item[k] for k in ('path', 'section', 'sha256')} | {'reason': 'character_budget', 'characters': size})
            continue
        # Indexed optional notes must match the task; legacy shared memory remains available.
        if not item['required'] and item['source_hashes'] and query and not item['relevance']:
            omitted.append({k: item[k] for k in ('path', 'section', 'sha256')} | {'reason': 'not_relevant'})
            continue
        selected.append(item)
        used += size
    if omitted:
        warnings.append('Some context was omitted; inspect reasons. Task goal/constraints/acceptance are never truncated.')
    return {'version': 1, 'max_characters': max_chars, 'characters_used': used,
            'selected': selected, 'omitted': omitted, 'warnings': warnings}


def manifest(pack):
    return dict(pack, selected=[{k: v for k, v in x.items() if k != 'content'} for x in pack['selected']])


def render(pack):
    return '\n\n'.join(x['path'] + ' [section ' + str(x['section']) + ']\n' + x['content']
                       for x in pack['selected']) + '\n\nContext omissions: ' + json.dumps(pack['omitted'], ensure_ascii=False)
