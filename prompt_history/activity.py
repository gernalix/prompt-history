"""Read-only activity aggregates; no conversation text leaves this module."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Europe/Copenhagen')
PLATFORMS = ('chatgpt', 'desktop', 'cli', 'unknown')


def readonly(path):
    return sqlite3.connect(Path(path).expanduser().resolve().as_uri() + '?mode=ro', uri=True)


def origins(path):
    with closing(readonly(path)) as conn:
        return {r[0]: (r[1], r[2]) for r in conn.execute('SELECT id,source,originator FROM threads')}


def classify(origin):
    source, originator = origin or (None, None)
    if source and (source.startswith('{') or source == 'exec'):
        return None  # workers and unattended automation are not personal activity
    if originator == 'Codex Desktop':
        return 'desktop'
    if source == 'cli' or originator == 'codex-tui':
        return 'cli'
    if originator and originator not in {'Android Studio', 'codex-chrome-extension-sidepanel'}:
        return None
    return 'unknown'


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(ZONE) if parsed.tzinfo else None
    except (ValueError, AttributeError, OverflowError):
        return None


def aggregate(db, thread_db, *, today=None):
    """Use canonical ChatGPT IDs and multiset matching of mirrored Codex text.

    Native public items take precedence. Each identical Bandit occurrence cancels
    one native occurrence in the same session/role, preserving repeated messages.
    Only hashes/counts are read, never tool payloads or full message metadata.
    """
    today = today or datetime.now(ZONE).date()
    origin = origins(thread_db)
    buckets = defaultdict(lambda: {'characters': 0, 'messages': 0, 'conversations': set()})
    mirrored = Counter()
    quality = Counter()
    with closing(readonly(db)) as conn:
        conn.execute('BEGIN')
        refreshed = conn.execute('SELECT max(ingested_at) FROM source_records').fetchone()[0]
        query = '''SELECT source,conversation_id,message_id,role,created_at,prompt_hash,
            length(prompt_text),{visibility}, {recipient}
            FROM prompts WHERE source=? AND role IN ('user','assistant')
            ORDER BY rowid'''
        for source in ('codex-native-history', 'codex-session-bandit', 'chatgpt'):
            fields = query.format(
                visibility="json_extract(metadata_json,'$.activity_chars')" if source == 'chatgpt' else 'NULL',
                recipient="json_extract(metadata_json,'$.recipient')" if source == 'chatgpt' else 'NULL')
            for _, conversation, message, role, created, digest, size, visible, recipient in conn.execute(fields, (source,)):
                if not conversation:
                    quality['missing_conversation'] += 1
                    continue
                platform = 'chatgpt' if source == 'chatgpt' else classify(origin.get(conversation))
                if platform is None:
                    quality['automation_excluded'] += 1
                    continue
                if source == 'chatgpt':
                    if visible is None:
                        quality['visibility_pending'] += 1
                        continue
                    size = visible
                    if recipient not in (None, 'all'):
                        continue
                elif source == 'codex-native-history':
                    mirrored[(conversation, role, digest)] += 1
                else:
                    key = (conversation, role, digest)
                    if mirrored[key]:
                        mirrored[key] -= 1
                        quality['mirrors_excluded'] += 1
                        continue
                moment = timestamp(created)
                if moment is None:
                    quality['missing_timestamp'] += 1
                    continue
                if not size or moment.date() > today:
                    continue
                day = moment.date().isoformat()
                bucket = buckets[(day, platform, role)]
                bucket['characters'] += size
                bucket['messages'] += 1
                bucket['conversations'].add((source == 'chatgpt', conversation))
        rows = [{'day': day, 'platform': platform, 'role': role,
                 'characters': b['characters'], 'messages': b['messages'],
                 # Opaque local identities are replaced with integers before delivery.
                 'conversation_ids': sorted(b['conversations'])}
                for (day, platform, role), b in sorted(buckets.items())]
    identities = {value: index for index, value in enumerate(sorted({v for b in buckets.values() for v in b['conversations']}))}
    for row in rows:
        row['conversation_ids'] = [identities[tuple(v)] for v in row['conversation_ids']]
    return {'rows': rows, 'quality': dict(quality), 'source_updated_at': refreshed,
            'generated_at': datetime.now(timezone.utc).isoformat(), 'today': today.isoformat(),
            'timezone': str(ZONE)}


def summarize(data, period='30', author='both', metric='characters'):
    if period not in {'7', '30', '90', 'all'} or author not in {'both', 'user', 'assistant'} or metric not in {'characters', 'messages', 'conversations'}:
        raise ValueError('invalid filter')
    today = date.fromisoformat(data['today'])
    first = min((date.fromisoformat(r['day']) for r in data['rows']), default=today)
    start = first if period == 'all' else today - timedelta(days=int(period)-1)
    # Include six days preceding the selected period in the rolling mean.
    days = {}
    cursor = start - timedelta(days=6)
    while cursor <= today:
        days[cursor.isoformat()] = {p: 0 for p in PLATFORMS}
        cursor += timedelta(days=1)
    conversations = defaultdict(set)
    total_conversations = set()
    for row in data['rows']:
        if row['day'] not in days or (author != 'both' and row['role'] != author):
            continue
        key = (row['day'], row['platform'])
        if metric == 'conversations':
            conversations[key].update(row['conversation_ids'])
        else:
            days[row['day']][row['platform']] += row[metric]
        if row['day'] >= start.isoformat():
            total_conversations.update(row['conversation_ids'])
    for (day, platform), values in conversations.items():
        days[day][platform] = len(values)
    out = []
    totals = [sum(values.values()) for values in days.values()]
    for index, (day, values) in enumerate(days.items()):
        if day >= start.isoformat():
            out.append({'day': day, **values, 'total': totals[index],
                        'average7': sum(totals[max(0,index-6):index+1])/7})
    return {**{k:v for k,v in data.items() if k != 'rows'}, 'days': out,
            'active_days': sum(r['total'] > 0 for r in out),
            'total': len(total_conversations) if metric == 'conversations' else sum(r['total'] for r in out),
            'metric': metric}
