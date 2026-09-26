from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import httpx


def dcg(relevances: list[int], k: int) -> float:
    return sum(rel / math.log2(index + 2) for index, rel in enumerate(relevances[:k]))


def metrics(rankings: list[list[str]], relevant: list[set[str]], ks=(5, 10, 20)) -> dict:
    out = {}
    for k in ks:
        recalls = []
        precisions = []
        hits = []
        ndcgs = []
        for ranking, truth in zip(rankings, relevant):
            top = ranking[:k]
            matched = [1 if item in truth else 0 for item in top]
            recalls.append(sum(matched)/max(1,len(truth)))
            precisions.append(sum(matched)/k)
            hits.append(1.0 if any(matched) else 0.0)
            ideal=[1]*min(k,len(truth))
            ndcgs.append(dcg(matched,k)/max(dcg(ideal,k),1e-12))
        out[f"recall@{k}"]=sum(recalls)/max(1,len(recalls))
        out[f"precision@{k}"]=sum(precisions)/max(1,len(precisions))
        out[f"hitrate@{k}"]=sum(hits)/max(1,len(hits))
        out[f"ndcg@{k}"]=sum(ndcgs)/max(1,len(ndcgs))
    reciprocal = []
    for ranking, truth in zip(rankings,relevant):
        rr = 0.0
        for idx,item in enumerate(ranking,1):
            if item in truth:
                rr = 1.0 / idx
                break
        reciprocal.append(rr)
    out["mrr"]=sum(reciprocal)/max(1,len(reciprocal))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("--url", default="http://localhost:8090/api/v1/search")
    parser.add_argument("--token", default=None)
    args=parser.parse_args()
    cases = [
        json.loads(line)
        for line in Path(args.dataset).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rankings = []
    truth = []
    headers = {"X-API-Key": args.token} if args.token else {}
    for case in cases:
        response = httpx.post(args.url, json={
            "query": case["query"],
            "organization_id": case["organization_id"],
            "wiki_id": case["wiki_id"],
            "limit": 20,
            "filters": case.get("filters", {}),
        }, headers=headers, timeout=120)
        response.raise_for_status()
        data = response.json()
        rankings.append(
            [f"{item['document_id']}::{item['section_id']}" for item in data["results"]]
        )
        truth.append(set(case["relevant"]))
    print(json.dumps(metrics(rankings, truth), indent=2))


if __name__ == "__main__":
    main()
