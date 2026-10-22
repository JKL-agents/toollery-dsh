"""ToolRet selection benchmark helpers; labels never enter tool preparation."""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import random
import statistics
import time

from .data import Dataset, ToolPool
from .metrics import evaluate
from .retrieval import BM25Index, REPRESENTATIONS, RankedTool, ToolSearch, build_documents
from .storage import read_jsonl


def effective_dataset(root: Path):
    original = Dataset.load(root / 'benchmark/toolret/development')
    prior = json.loads((root / 'docs/experiments/toolret-protocol.json').read_text('utf-8'))
    omitted = prior['generation_coverage']
    tools = set(omitted['excluded_tool_ids'])
    queries = set(omitted['excluded_query_ids'])
    pool = ToolPool([tool for key, tool in original.pool.tools.items() if key not in tools])
    selected = [query for query in original.queries if query.query_id not in queries]
    qrels = {query.query_id: original.qrels[query.query_id] for query in selected}
    if any(set(labels) - set(pool.tool_ids) for labels in qrels.values()):
        raise ValueError('Effective query has a positive outside the candidate pool')
    if pool.snapshot_hash != prior['effective_snapshot_hash']:
        raise ValueError('Effective snapshot changed')
    meta = {row['id']: {key: row.get(key, 'unknown') for key in ('category', 'source_dataset')}
            for row in read_jsonl(root / 'benchmark/toolret/development/queries.jsonl')}
    return Dataset(pool, selected, qrels, original.provenance), meta, omitted


def presentation_schemas(pool):
    """Order-preserving unique provider names; untouched raw spec in description.

    Empty parameter schemas intentionally expose no invented executable contract.
    The original heterogeneous signature remains readable inside documentation.
    """
    mapping = {key: f'tr_{index:05d}' for index, key in enumerate(pool.tool_ids)}
    schemas = [{'name': mapping[key], 'description': pool.tools[key].spec_text,
                'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
               for key in pool.tool_ids]
    return schemas, mapping


class MappedIndex:
    def __init__(self, index, mapping):
        self.index, self.mapping = index, mapping

    def search(self, query, limit=10):
        return [RankedTool(self.mapping[hit.tool_id], hit.score) for hit in self.index.search(query, limit)]


def attach_indexes(bridge, session_id, indexes, mapping, preparation):
    state = bridge.sessions[session_id]
    if set(state.pool.tool_ids) != set(mapping.values()):
        raise ValueError('DSH did not discover the complete mapped pool')
    state.searches = {rep: ToolSearch(state.pool, MappedIndex(index, mapping)) for rep, index in indexes.items()}
    state.preparation, state.ready = preparation, True


def stratified_sample(dataset, meta, count=200, seed=20261021):
    """Largest-remainder proportional sample, at least one in every nonempty stratum."""
    groups = defaultdict(list)
    for query in dataset.queries:
        info = meta[query.query_id]
        key = (info['category'], query.source_dataset, 'single' if len(dataset.qrels[query.query_id]) == 1 else 'multi')
        groups[key].append(query.query_id)
    if not len(groups) <= count <= len(dataset.queries):
        raise ValueError('Sample size cannot cover strata')
    quotas = {key: 1 for key in groups}
    while sum(quotas.values()) < count:
        key = max((key for key in groups if quotas[key] < len(groups[key])),
                  key=lambda key: (count * len(groups[key]) / len(dataset.queries) - quotas[key], key))
        quotas[key] += 1
    rng = random.Random(seed)
    selected = []
    strata = []
    for key in sorted(groups):
        selected.extend(sorted(rng.sample(sorted(groups[key]), quotas[key])))
        strata.append({'category': key[0], 'source': key[1], 'positives': key[2],
                       'population': len(groups[key]), 'sample': quotas[key]})
    return sorted(selected), strata


def distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0}
    def percentile(p):
        i = (len(values) - 1) * p
        low = math.floor(i)
        return values[low] + (values[math.ceil(i)] - values[low]) * (i - low)
    return {'n': len(values), 'mean': statistics.fmean(values), 'p50': percentile(.5),
            'p95': percentile(.95), 'p99': percentile(.99), 'max': values[-1]}


def retrieval_rows(runs, dataset):
    rows = {qid: {} for qid in runs}
    for k in (1, 3, 5, 10):
        scored = evaluate(runs, dataset.qrels, k=k)['per_query']
        for qid in rows:
            rows[qid].update({key: value for key, value in scored[qid].items() if key != 'returned_tools'})
            rows[qid][f'complete@{k}'] = float(set(dataset.qrels[qid]) <= {hit.tool_id for hit in runs[qid][:k]})
    for qid in rows:
        rows[qid]['returned'] = len(runs[qid])
        rows[qid]['empty'] = float(not runs[qid])
    return rows


def aggregate_rows(rows, meta, qrels):
    groups = defaultdict(list)
    for qid, row in rows.items():
        for group in ('overall', 'category/' + str(meta[qid]['category']),
                      'source/' + str(meta[qid]['source_dataset']),
                      'positives/' + ('single' if len(qrels[qid]) == 1 else 'multi')):
            groups[group].append(row)
    return {group: {'n': len(items), **{key: statistics.fmean(item[key] for item in items)
            for key in items[0]}} for group, items in sorted(groups.items())}


def parse_selection(text, exposed):
    """Accept a JSON object, or one fenced JSON object; never salvage arbitrary IDs."""
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        value = json.loads(text)
        ids = value['tool_ids']
        if not isinstance(ids, list) or any(not isinstance(key, str) for key in ids):
            raise ValueError('tool_ids must be strings')
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate tool IDs')
    except (ValueError, TypeError, KeyError) as error:
        return [], 'parse_failure: ' + str(error)
    if set(ids) - set(exposed):
        return ids, 'outside_exposed_candidates'
    return ids, None


def selection_score(predicted, relevant):
    hits = len(set(predicted) & set(relevant))
    precision = hits / len(set(predicted)) if predicted else 0.
    recall = hits / len(relevant)
    return {'precision': precision, 'recall': recall,
            'f1': 2 * precision * recall / (precision + recall) if precision + recall else 0.,
            'success': float(hits > 0), 'complete': float(set(relevant) <= set(predicted))}
