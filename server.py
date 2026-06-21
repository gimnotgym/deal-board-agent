"""
Deal Board Agent — FastAPI Server
SK그룹 AX 과제 MVP
"""

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ── 성능 최적화: 캐시 ──
from time import time
_cache = {}
_cache_ttl = 3600  # 1시간 — 데모 중 캐시 만료 방지 (데이터는 사용자 액션으로만 변경)

def cache_get(key):
    """캐시에서 값 조회 (TTL 확인)"""
    if key in _cache:
        val, ts = _cache[key]
        if time() - ts < _cache_ttl:
            return val
        else:
            del _cache[key]
    return None

def cache_set(key, val):
    """캐시에 값 저장"""
    _cache[key] = (val, time())

def cache_clear(opp_id=None):
    """캐시 초기화 — opp_id 지정 시 해당 딜 + 파이프라인만 삭제, 미지정 시 전체"""
    if opp_id:
        _cache.pop(f"opp_{opp_id}", None)
        # 파이프라인 캐시(pipeline_*)는 전체 삭제 (키 패턴 다양)
        for k in [k for k in _cache if k.startswith("pipeline_") or k.startswith("opps_")]:
            _cache.pop(k, None)
    else:
        _cache.clear()

# ── Anthropic 클라이언트 ──
try:
    import anthropic
    # 타임아웃 12초 + 재시도 0회 — 크레딧 부족/네트워크 지연 시 빠르게 폴백으로 전환
    _client = anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        timeout=12.0,
        max_retries=0,
    )
    ANTHROPIC_OK = True
except Exception:
    _client = None
    ANTHROPIC_OK = False

# ── 데모 픽스처 (신한은행 시나리오 고정) ──
_DEMO_OPP_ID = "OPP-2026-125"

_DEMO_FIXTURE_1 = {  # 문서1: 회의록_0528
    "summary": "5월 28일 신한은행 AI 플랫폼 설명회를 분석했습니다.\n연간 120만 시간 절감 기대성과와 80억 원 예산이 공식 확인됐고, 기술 40%·레퍼런스 30%·가격 30% 평가 배점도 파악됐습니다. 박성민 팀장이 레퍼런스 자료를 직접 요청해 내부 지지자로 판단됩니다.",
    "changes": [
        {"code": "M", "from": 1, "to": 3, "direction": "up", "evidence": "연간 약 120만 시간 소요 / 여신심사보고서 초안 생성, 고객상담 자동 요약", "reason": "기대성과 수치와 적용 업무 구체적 명시"},
        {"code": "E", "from": 2, "to": 3, "direction": "up", "evidence": "총 예산: 80억 원 (소프트웨어 라이선스 + 구축 용역)", "reason": "총예산 공식 확인"},
        {"code": "DC", "from": 0, "to": 2, "direction": "up", "evidence": "기술 완성도 40% / 금융권 레퍼런스 30% / 가격 경쟁력 30%", "reason": "평가 기준 배점 명확히 확인"},
    ],
    "ambiguities": [{"item": "CO", "trigger": "경쟁 벤더 수 미확인", "is_stage_required": False, "question": "설명회에 당사외 어떤 벤더들이 참석했는지 파악하셨나요? 경쟁사가 중요할 것 같네요."}],
}

_DEMO_FIXTURE_2 = {  # 문서2: 통화요약_0609
    "scores": {"E": 3, "C": 2, "M": 3, "DC": 2, "DP": 2, "PP": 0, "I": 2, "CO": 1},
    "changes": [
        {"item": "DP", "from": 0, "to": 2, "direction": "up", "evidence": "CTO→CFO 협의→6월 30일 경영위원회 보고로 최종 승인 예정", "reason": "의사결정 승인 경로 직접 확인"},
        {"item": "C",  "from": 1, "to": 2, "direction": "up", "evidence": "이번 사업 최종 결정권자: 이준혁 전무", "reason": "실질 의사결정권자 직접 확인"},
    ],
    "ambiguities": [{"item": "I", "trigger": "내부 설득이 관건", "is_stage_required": False, "question": "CTO가 '내부 설득이 관건'이라고 하셨는데 — 회의록에서 여신심사팀 최현우 차장이 평가위원에 포함됐잖아요. 이 분이 우리 솔루션에 우호적인지, PT에서 어떤 포인트를 강조해야 할지 감이 오시나요?"}],
    "preview_text": "이준혁 CTO와의 직접 통화로 의사결정 구조가 확인됐습니다. CTO 검토 → CFO 협의 → 6월 30일 경영위원회 보고로 이어지는 승인 경로가 확정됐으며, CTO가 실제 업무 프로세스 기반의 라이브 데모를 직접 요청했습니다.",
    "champion_type": "Type1 실무추진자", "champion_risk": None, "next_action": "6월 30일 PT 확정 회신 및 라이브 데모 시나리오 구성", "recommended_stage": None,
}

_DEMO_FIXTURE_3 = {  # Step 3: 최 차장 답변
    "scores": {"E": 3, "C": 3, "M": 3, "DC": 2, "DP": 2, "PP": 0, "I": 2, "CO": 1},
    "changes": [
        {"item": "C", "from": 2, "to": 3, "direction": "up", "evidence": "최 차장, 차세대 시스템 구축 시 당사 시스템에 매우 만족", "reason": "여신심사팀 차장을 복수 챔피언으로 확인"},
    ],
    "ambiguities": [],
    "preview_text": "여신심사팀 최현우 차장이 기존 당사 시스템에 만족도가 높은 것으로 확인됐습니다. CTO와 함께 복수 챔피언 구조가 형성됐으며, PT에서 차세대 구축 경험을 강조하는 프레이밍이 효과적일 것으로 판단됩니다.",
    "champion_type": "Type1 실무추진자", "champion_risk": None, "next_action": None, "recommended_stage": None,
}

_DEMO_CHAT_3 = "좋은 신호입니다. 최 차장이 기존 협업에 긍정적이라면 여신심사팀 저항은 우려보다 훨씬 낮을 수 있어요.\n\nPT 전략 제안: 도입부에 차세대 구축 성과 수치를 먼저 배치하고, 이번 GenAI가 그 위에 얹히는 구조임을 강조하세요. '새로운 시스템'이 아닌 '기존 투자의 확장'으로 프레이밍하면 최 차장이 내부에서 설득하기 쉬워집니다.\n\nC(내부 추진자)에 최 차장을 CTO와 함께 복수 챔피언으로 업데이트했습니다."

def _is_demo_opp(opp_id: str) -> bool:
    return opp_id == _DEMO_OPP_ID

def _is_demo_doc1(text: str) -> bool:
    return "120만 시간" in text or "신한은행 AI 플랫폼 도입 설명회" in text

def _is_demo_doc2(text: str) -> bool:
    return "이준혁" in text and ("CTO" in text or "통화" in text)

def _is_demo_step3(text: str) -> bool:
    return "최 차장" in text and "차세대" in text

# ── 앱 설정 ──
app = FastAPI(title="Deal Board Agent", version="1.0.0")

@app.on_event("startup")
async def init_data():
    """data/ 폴더가 없거나 파일이 없으면 data_seed/에서 복사"""
    import shutil
    DATA.mkdir(exist_ok=True)
    SEED = BASE / "data_seed"
    if SEED.exists():
        for seed_file in SEED.glob("*.json"):
            target = DATA / seed_file.name
            if not target.exists():
                shutil.copy2(seed_file, target)
                print(f"[startup] seed 복사: {seed_file.name}")
    print("[startup] data/ 초기화 완료")

async def _do_warm_cache():
    """백그라운드에서 캐시 워밍 — 서버 시작을 블로킹하지 않음"""
    _w0 = time()
    try:
        opps = load("opportunities")
        closed_deals = load("closed_deals")
        _rep_idx = _build_closed_index(closed_deals)  # O(n) 인덱스 사전 생성
        cache_set("closed_deals_all", closed_deals)    # closed_deals도 캐시
        STAGE_N = STAGE_NORMAL_DAYS
        for o in opps:
            o["score"] = calc_deal_score(o, closed_deals, _rep_idx)
            normal = STAGE_N.get(o.get("stage", "Lead"), 30)
            days = o.get("stage_days", 0)
            o["stage_alert"] = (
                "danger"  if normal > 0 and days > normal * 2 else
                "warning" if normal > 0 and days > normal * 1.5 else
                "normal"
            )
        cache_set("opps_all_all_all_all_all_all", opps)
        for o in opps:
            cache_set(f"opp_{o['id']}", o)
        print(f"[startup] {len(opps)}개 딜 스코어 계산 완료 ({(time()-_w0)*1000:.0f}ms)")

        # 사업부문장/리더 브리핑 캐시 사전 계산 (첫 방문 시 즉시 응답)
        try:
            import datetime as _dt
            _today = _dt.date.today()
            _gap = calc_pipeline_gap(opps)
            _risky = sorted(
                [o for o in opps if o["score"]["total"] < 40 or o.get("stage_alert") == "danger"],
                key=lambda x: x["score"]["total"]
            )[:5]
            _urgent = []
            for o in opps:
                try:
                    _d = _dt.date.fromisoformat(o["입찰일"])
                    _days = (_d - _today).days
                    if 0 <= _days <= 30:
                        o["_days_to_bid"] = _days
                        _urgent.append(o)
                except Exception:
                    pass
            _urgent.sort(key=lambda x: x.get("_days_to_bid", 99))
            _pre = {
                "briefing": _fallback_briefing(opps, _gap, _risky, _urgent),
                "risky_deals": _risky[:3],
                "urgent_deals": _urgent[:3],
                "gap": _gap,
            }
            cache_set("briefing_exec_all_all", _pre)
            cache_set("briefing_leader_all_all", _pre)

            # pipeline_all_all 사전 계산 (사업부문장 페이지)
            _STAGES = ["Lead","Identified","Identified-Remind","Identified-Registered",
                       "Validated","Qualified","Negotiated","우선협상","계약완료"]
            def _build_pipeline(opp_list):
                _waterfall = []
                for s in _STAGES:
                    s_opps = [o for o in opp_list if o["stage"] == s]
                    _waterfall.append({
                        "stage": s, "count": len(s_opps),
                        "total": sum(o["수주목표액"] for o in s_opps),
                        "weighted": round(sum(o["수주목표액"]*o.get("수주확도",0)/100 for o in s_opps),1),
                    })
                _all_scores_flat = [o["score"]["total"] for o in opp_list if o.get("score")]
                _team_avg = round(sum(_all_scores_flat)/len(_all_scores_flat),1) if _all_scores_flat else 0
                _all_reps = list(set(o.get("영업대표","") for o in opp_list))
                _rep_summary = []
                for r in _all_reps:
                    r_opps = [o for o in opp_list if o.get("영업대표") == r]
                    r_scores = [o["score"]["total"] for o in r_opps if o.get("score")]
                    avg = round(sum(r_scores)/len(r_scores),1) if r_scores else 0
                    _rep_summary.append({
                        "name": r, "deal_count": len(r_opps),
                        "total_amount": sum(o["수주목표액"] for o in r_opps),
                        "avg_score": avg, "score_alert": avg < _team_avg - 15,
                    })
                return {"gap": calc_pipeline_gap(opp_list), "waterfall": _waterfall,
                        "rep_summary": _rep_summary, "team_avg_score": _team_avg}

            cache_set("pipeline_all_all", _build_pipeline(opps))

            # dept별 opps + pipeline + briefing 사전 계산
            _depts = list(set(o.get("사업부문","") for o in opps if o.get("사업부문")))
            for _dept in _depts:
                _d_opps = [o for o in opps if o.get("사업부문") == _dept]
                cache_set(f"opps_{_dept}_all_all_all_all_all", _d_opps)
                cache_set(f"pipeline_{_dept}_all", _build_pipeline(_d_opps))
                _d_gap = calc_pipeline_gap(_d_opps)
                _d_risky = sorted([o for o in _d_opps if o["score"]["total"] < 40], key=lambda x: x["score"]["total"])[:5]
                _d_urgent = sorted([o for o in _d_opps if o.get("_days_to_bid") is not None], key=lambda x: x.get("_days_to_bid", 99))[:5]
                _d_pre = {
                    "briefing": _fallback_briefing(_d_opps, _d_gap, _d_risky, _d_urgent),
                    "risky_deals": _d_risky[:3], "urgent_deals": _d_urgent[:3], "gap": _d_gap,
                }
                cache_set(f"briefing_exec_{_dept}_all", _d_pre)
                cache_set(f"briefing_leader_{_dept}_all", _d_pre)

            print(f"[startup] 전체 캐시 워밍 완료 — {(time()-_w0)*1000:.0f}ms (dept: {_depts})")
        except Exception as be:
            print(f"[startup] 브리핑 사전계산 실패: {be}")

        # 관리자 페이지 사전 캐시
        try:
            conflicts = load("conflict_log")
            conflicts.sort(key=lambda x: x.get("timestamp",""), reverse=True)
            cache_set("admin_conflicts_all_all", conflicts)
            cache_set("admin_fewshots", load_fewshots())
            cache_set("admin_prompt", {"prompt": load_prompt()})
            print("[startup] 관리자 페이지 캐시 완료")
        except Exception as ae:
            print(f"[startup] 관리자 캐시 실패: {ae}")

    except Exception as e:
        print(f"[startup] 캐시 워밍 실패: {e}")

@app.on_event("startup")
async def warm_cache():
    """서버 시작을 블로킹하지 않고 백그라운드에서 캐시 워밍"""
    import asyncio
    asyncio.create_task(_do_warm_cache())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

@app.get("/api/debug/cache")
async def debug_cache():
    """캐시 상태 확인 — 어떤 키가 캐시에 있는지 확인"""
    keys = list(_cache.keys())
    return {
        "cached_keys": keys,
        "count": len(keys),
        "has_opps_all": "opps_all_all_all_all_all_all" in _cache,
        "has_pipeline_all": "pipeline_all_all" in _cache,
        "has_briefing_exec": any(k.startswith("briefing_exec") for k in keys),
        "has_briefing_leader": any(k.startswith("briefing_leader") for k in keys),
        "has_pipeline_dept": any(k.startswith("pipeline_") and k != "pipeline_all_all" for k in keys),
    }

BASE = Path(__file__).parent
DATA = BASE / "data"

# ── 데이터 로더 ──
def load(name: str):
    p = DATA / f"{name}.json"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def save(name: str, data):
    p = DATA / f"{name}.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_fewshots():
    p = BASE / "fewshot_examples.json"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def save_fewshots(data):
    p = BASE / "fewshot_examples.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_prompt():
    p = BASE / "agent_prompt.md"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")

def save_prompt(text: str):
    p = BASE / "agent_prompt.md"
    p.write_text(text, encoding="utf-8")

# ── Scoring 엔진 ──
STAGE_NORMAL_DAYS = {
    "Lead": 45, "Identified": 60, "Identified-Remind": 30,
    "Identified-Registered": 30, "Validated": 14, "Qualified": 7,
    "Negotiated": 7, "우선협상": 7, "계약완료": 0,
}
# 기본 가중치 (파일 없을 때 폴백)
_DEFAULT_WEIGHTS = {
    "E": 2.0, "C": 2.0, "M": 1.5, "DC": 1.5,
    "DP": 1.5, "I": 1.5, "PP": 1.0, "CO": 1.0,
}
KEY_ACCOUNTS = {
    "하나은행","신한은행","KB국민은행","우리은행","NH농협은행",
    "IBK기업은행","카카오뱅크","토스뱅크","현대카드","교보생명",
    "롯데쇼핑","이마트","쿠팡","현대글로비스","대한항공","SK스토아","삼성생명",
}

_weights_cache: dict = {}

def load_meddpicc_weights() -> dict:
    if _weights_cache:
        return dict(_weights_cache)
    p = DATA / "meddpicc_weights.json"
    if not p.exists():
        _weights_cache.update(_DEFAULT_WEIGHTS)
        return dict(_DEFAULT_WEIGHTS)
    try:
        with open(p, encoding="utf-8") as f:
            w = json.load(f)
        merged = {k: w.get(k, _DEFAULT_WEIGHTS[k]) for k in _DEFAULT_WEIGHTS}
        _weights_cache.update(merged)
        return merged
    except Exception:
        _weights_cache.update(_DEFAULT_WEIGHTS)
        return dict(_DEFAULT_WEIGHTS)

def invalidate_weights_cache():
    _weights_cache.clear()

def save_meddpicc_weights(weights: dict):
    p = DATA / "meddpicc_weights.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(weights, f, ensure_ascii=False, indent=2)

# ── 5-Factor Deal Score ──────────────────────────────────────────────────────

def calc_strategic_fit(opp: dict) -> float:
    """Factor 1 · 전략적합도 (20점)
    중점사업여부: 10점 / 중점고객여부: 6점 / AX사업여부: 가점 4점
    """
    score = 0.0
    if opp.get("중점사업여부"):  score += 10.0
    if opp.get("중점고객여부"):  score += 6.0
    if opp.get("AX사업여부"):    score += 4.0
    return round(min(score, 20.0), 2)

def calc_deal_quality(meddpicc: dict, weights: dict = None) -> float:
    """Factor 2 · 딜 품질 — MEDDPICC (30점)
    가중합 / 36(이론적 최대) × 30
    """
    w = weights or load_meddpicc_weights()
    max_score = sum(3 * v for v in w.values())   # 36
    raw = sum(meddpicc.get(k, 0) * w.get(k, 0) for k in w)
    return round((raw / max_score) * 30.0, 2) if max_score else 0.0

def calc_activity(opp: dict) -> float:
    """Factor 3 · 활동성 (15점)
    최근 30일 activity_log 업데이트 횟수 기준
    0회→0 / 1회→3 / 2~3회→7 / 4~6회→11 / 7회+→15
    """
    from datetime import date, timedelta
    today = date.today()
    cutoff = today - timedelta(days=30)
    log = opp.get("activity_log", [])
    recent = sum(1 for e in log if e.get("date","") >= cutoff.strftime("%Y-%m-%d"))
    if recent == 0:   return 0.0
    if recent == 1:   return 3.0
    if recent <= 3:   return 7.0
    if recent <= 6:   return 11.0
    return 15.0

def calc_customer_history(opp: dict, closed_deals: list) -> float:
    """Factor 4 · 고객사 이력 (20점)
    계약건수 기본점수(0~15) + 누적계약규모 보정(0~5)
    """
    customer = opp.get("고객사명", "")
    past = [d for d in closed_deals
            if d.get("고객사명") == customer and d.get("결과") == "계약완료"]
    count = len(past)
    total_amt = sum(d.get("계약금액", 0) for d in past)

    # 건수 기본 점수
    if count == 0:   base = 2.0
    elif count == 1: base = 6.0
    elif count == 2: base = 10.0
    else:            base = 15.0

    # 규모 보정
    if total_amt >= 150:   bonus = 5.0
    elif total_amt >= 50:  bonus = 2.0
    else:                  bonus = 0.0

    return round(min(base + bonus, 20.0), 2)

def _build_closed_index(closed_deals: list) -> dict:
    """closed_deals를 rep별로 미리 그룹핑 — O(n²) → O(n) 최적화"""
    idx: dict = {}
    for d in closed_deals:
        rep = d.get("영업대표", "")
        result = d.get("결과", "")
        if result not in ("계약완료", "Deal Lost"):
            continue
        if rep not in idx:
            idx[rep] = {"total": 0, "won": 0}
        idx[rep]["total"] += 1
        if result == "계약완료":
            idx[rep]["won"] += 1
    return idx

def calc_rep_rate(opp: dict, closed_deals: list = None, _rep_idx: dict = None) -> float:
    """Factor 5 · 영업대표 승률 (15점)
    승률 기본점수(0~12) + 신뢰도 보정(0~3)
    이력 3건 미만 → 6점 고정
    """
    rep = opp.get("영업대표", "")
    if _rep_idx is not None:
        entry = _rep_idx.get(rep, {"total": 0, "won": 0})
        n = entry["total"]
        won = entry["won"]
    else:
        rep_deals = [d for d in (closed_deals or [])
                     if d.get("영업대표") == rep and d.get("결과") in ("계약완료", "Deal Lost")]
        n = len(rep_deals)
        won = sum(1 for d in rep_deals if d.get("결과") == "계약완료")

    if n < 3:
        opp["_rep_win_rate"] = None
        opp["_rep_deal_count"] = n
        return 6.0

    win_rate = won / n
    opp["_rep_win_rate"] = round(win_rate * 100)
    opp["_rep_deal_count"] = n

    if win_rate < 0.20:   base = 2.0
    elif win_rate < 0.30: base = 4.0
    elif win_rate < 0.40: base = 6.0
    elif win_rate < 0.50: base = 8.0
    elif win_rate < 0.60: base = 10.0
    elif win_rate < 0.75: base = 11.0
    else:                 base = 12.0

    if n <= 2:    reliability = 0.0
    elif n <= 5:  reliability = 1.0
    elif n <= 10: reliability = 2.0
    else:         reliability = 3.0

    return round(min(base + reliability, 15.0), 2)

def calc_deal_score(opp: dict, closed_deals: list, _rep_idx: dict = None) -> dict:
    weights = load_meddpicc_weights()
    sf = calc_strategic_fit(opp)
    dq = calc_deal_quality(opp.get("meddpicc", {}), weights)
    ac = calc_activity(opp)
    ch = calc_customer_history(opp, closed_deals)
    rr = calc_rep_rate(opp, closed_deals, _rep_idx)
    total = round(sf + dq + ac + ch + rr, 1)
    return {
        "strategic_fit": sf,
        "deal_quality":  dq,
        "activity":      ac,
        "cust_history":  ch,
        "rep_rate":      rr,
        "total":         total
    }

def calc_pipeline_gap(opps: list) -> dict:
    target = 5000
    contracted = sum(o["수주목표액"] for o in opps if o["stage"] == "계약완료")
    preferred  = sum(o["수주목표액"] * 0.9 for o in opps if o["stage"] == "우선협상")
    weighted   = sum(
        o["수주목표액"] * (o.get("수주확도", 0) / 100)
        for o in opps
        if o["stage"] not in ("계약완료", "우선협상")
    )
    expected = contracted + preferred + weighted
    gap = max(0, target - expected)
    return {
        "target": target,
        "contracted": round(contracted, 1),
        "preferred":  round(preferred, 1),
        "weighted":   round(weighted, 1),
        "expected":   round(expected, 1),
        "gap":        round(gap, 1),
        "achievement_rate": round(expected / target * 100, 1),
    }

# ══════════════════════════════════════════════
# MEDDPICC 평가 프롬프트 빌더
# ══════════════════════════════════════════════
STAGE_REQUIRED = {
    "Lead": [], "Identified": ["I"], "Identified-Remind": ["I"],
    "Identified-Registered": ["I", "E"], "Validated": ["I", "E", "M"],
    "Qualified": ["M", "DC", "CO"], "Negotiated": ["DP", "PP", "CO"],
    "우선협상": ["PP", "DP"], "계약완료": [],
}
STAGE_MAX_QUESTIONS = {
    "Lead": 0, "Identified": 1, "Identified-Remind": 1,
    "Identified-Registered": 2, "Validated": 2,
    "Qualified": 3, "Negotiated": 3, "우선협상": 2, "계약완료": 0,
}

def build_meddpicc_prompt(opp: dict, new_input: str, fewshots: list, conversation: list = []) -> str:
    stage = opp.get("stage", "Lead")
    required = STAGE_REQUIRED.get(stage, [])
    current  = opp.get("meddpicc", {E: 0 for E in "E C M DC DP PP I CO".split()})
    existing = opp.get("주요_진행사항", "")

    # Few-shot 블록
    fs_block = ""
    if fewshots:
        fs_block = "\n## 판단 예시 (아래 패턴을 참고하세요)\n"
        for fs in fewshots[:6]:
            fs_block += f"""
입력: "{fs['input']}"
항목: {fs['item']} → 정답 {fs['correct_score']}점 (LLM이 {fs['wrong_score']}점으로 틀린 케이스)
이유: {fs['reason']}
"""

    sys_prompt = f"""당신은 한국 B2B IT 영업 전문가입니다.
영업 담당자가 입력한 텍스트를 읽고 MEDDPICC 프레임워크로 딜 품질을 평가합니다.

평가 원칙:
1. 명시적으로 언급된 내용만 점수에 반영 (추측 금지)
2. 애매한 경우 낮은 점수 부여 (보수적 평가)
3. 한국 IT 영업 맥락 반영:
   - E: 단일 승인자가 아닌 의사결정 구조 파악 여부
   - "~래요/~대요/~한대요" = 전언, 직접 확인 아님 → 점수 제한
   - "야근이 많다", "주말에도 나온다" = 수작업 과다, 구체적 Pain
   - "감사 전에", "검사 받아야", "규정상 반드시" = 데드라인 Pain → I=3 가능
   - "현재 A사가 하고 있다" = 현재 SI업체 = 가장 강력한 경쟁자 → CO=2
   - "분기말에 집행" = 타임라인 확정에 준함 → DP=2
   - "조달청 등록", "나라장터" = PP=1 (공공 행정 절차)
   - "알아서 된다", "걱정 마세요" = PP=0 주의 (절차 미파악)
   - 단독 입찰 = CO=2 (유찰 리스크 있음, CO=3 아님)
   - 예산 확보 = E=2 (집행 권한자 미확인이면 E=3 아님)
   - C 유형: Type1(실무추진자)=2점이상, Type2(정보제공자)=1점, Type3(정치적지지자)=2점이상
   - 과거 확인된 사실은 단절 신호 없으면 유지 (현재 실무 과장 컨택 중이어도 과거 CIO 미팅은 유효)
4. 점수 변동 시 반드시 근거 문장 인용
5. 문서/회의록 입력 시 MEDDPICC 관련 정보만 추출
{fs_block}

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만."""

    # 이번 세션 대화 이력 (최근 10턴)
    conv_block = ""
    if conversation:
        lines = []
        for t in conversation[-10:]:
            role = "영업대표" if t.get("role") == "rep" else "AI"
            text = t.get("text", "")[:400]
            lines.append(f"{role}: {text}")
        conv_block = "\n## 이번 세션 대화 이력 (이미 파악된 내용 참고)\n" + "\n".join(lines)

    user_prompt = f"""## 딜 정보
사업기회: {opp.get('사업기회명', '')}
고객사: {opp.get('고객사명', '')} ({opp.get('산업', '')})
Stage: {stage}
사업유형: {opp.get('사업유형', '')}

## 기존 누적 정보
{existing}
{conv_block}

## 현재 입력 텍스트 (이번에 새로 추가된 내용)
{new_input}

## 현재 MEDDPICC 점수
E:{current.get('E',0)} C:{current.get('C',0)} M:{current.get('M',0)} DC:{current.get('DC',0)} DP:{current.get('DP',0)} PP:{current.get('PP',0)} I:{current.get('I',0)} CO:{current.get('CO',0)}

## 현재 Stage 필수 확정 항목
{required}

## 평가 지시
{{
  "scores": {{"E":0, "C":0, "M":0, "DC":0, "DP":0, "PP":0, "I":0, "CO":0}},
  "changes": [{{"item":"E","from":0,"to":1,"direction":"up","reason":"근거 문장"}}],
  "ambiguities": [{{"item":"E","trigger":"트리거 문장","is_stage_required":true,"question":"추가 질문"}}],
  "champion_type": "Type1 실무추진자 또는 Type2 정보제공자 또는 Type3 정치적지지자 또는 null",
  "champion_risk": "리스크 내용 또는 null",
  "preview_text": "오늘 확인된 핵심 내용을 2~3문장으로 요약. 반드시 새로운 문장으로 작성하고 원문을 그대로 인용하거나 잘라붙이지 말 것. 점수·숫자·MEDDPICC 항목명 언급 금지.",
  "next_action": "다음에 확인하면 좋을 것 한 가지 (자연어)",
  "recommended_stage": null
}}

## Stage 변경 규칙 (recommended_stage)
- 기본값 null (변경 불필요)
- 딜 취소·철회·포기·탈락·탈락통보 → "Deselected"
- 계약 완료·서명·수주 확정 → "계약완료"
- 우선협상대상자 선정 → "우선협상"
- 제안서 제출·PT 완료 → "Negotiated"
- 예산+의사결정자+Pain 모두 확인 → "Qualified"
- 현재 stage보다 하향이 명확할 때만 추천 (승격은 보수적으로)
- stage 목록: Lead, Identified, Validated, Qualified, Negotiated, 우선협상, 계약완료, Deselected"""

    return sys_prompt, user_prompt

def build_agent_chat_prompt(opp: dict, conversation: list, new_message: str) -> tuple:
    stage = opp.get("stage", "Lead")
    required = STAGE_REQUIRED.get(stage, [])
    max_q = STAGE_MAX_QUESTIONS.get(stage, 1)
    asked = sum(1 for t in conversation if t.get("role") == "agent" and "?" in t.get("text", ""))

    from datetime import date as _date
    today_str = _date.today().strftime("%Y년 %m월 %d일")
    sys_prompt = f"""당신은 SK그룹 영업팀의 딜 코치입니다.
오늘 날짜: {today_str}
영업대표와 자연스러운 대화를 통해 사업기회 정보를 파악하고, 딜 진행 상황을 함께 점검합니다.

## 대화 원칙
1. 상담가처럼 말하세요
   - 영업대표가 한 말을 먼저 인정하고 시작
   - 질문보다 관찰로 먼저 시작 ("아직 직접 만나신 건 아닌 것 같네요" → 영업대표가 확인/정정)
   - 다음 액션을 함께 제안

2. 점수/프레임워크 용어 절대 언급 금지
   - 금지: "E 점수", "MEDDPICC", "Champion 항목", "Win Potential", "1점", "2점"
   - 대신: "의사결정권자", "내부 추진하시는 분", "평가 기준", "수주 가능성"

3. 추가 질문은 한 번에 하나씩, Stage별 한도 내에서
   - 현재 Stage: {stage}
   - 이번 대화에서 남은 질문 가능 횟수: {max(0, max_q - asked)}회
   - 이 Stage에서 중점 확인 항목: {required if required else '없음 (자유 대화)'}
   - 한도 소진 시 "오늘 말씀해주신 내용 정리해드릴게요"로 마무리

4. 한국 IT 영업 맥락 파악
   - "~래요/~대요" = 전언 → "직접 말씀하신 건가요, 전해 들으신 건가요?" 확인
   - "야근이 많다" = Pain 신호 → 구체적 업무/시간 파악
   - "감사 전에", "규정상 반드시" = 강한 Pain/데드라인 → 일정 역산 제안
   - "현재 A사가 하고 있다" = 경쟁자 파악 → 차별점 확인
   - CIO 교체, 담당자 이동 → 관계 재구축 필요성 언급

5. 마무리는 자연어 요약 + "이대로 저장할까요?"

## 현재 딜 정보
사업기회: {opp.get('사업기회명', '')}
고객사: {opp.get('고객사명', '')} | Stage: {stage}
최근 진행사항: {opp.get('주요_진행사항', '')[:100]}"""

    messages = []
    for turn in conversation[-10:]:  # 최근 10턴
        role = "user" if turn.get("role") == "rep" else "assistant"
        messages.append({"role": role, "content": turn.get("text", "")})
    messages.append({"role": "user", "content": new_message})

    return sys_prompt, messages

def build_report_chat_prompt(opp: dict, new_message: str, viewer_role: str) -> tuple:
    """사업부문장/영업리더가 딜을 물을 때 — 영업대표가 입력한 정보를 '보고'한다."""
    m = opp.get("meddpicc", {})
    names = {"E":"예산 집행 권한","C":"내부 추진자","M":"기대 성과","DC":"평가 기준",
             "DP":"결정 절차","PP":"계약 절차","I":"핵심 Pain","CO":"경쟁 현황"}
    confirmed = [names[k] for k in names if m.get(k,0) >= 2]
    partial   = [names[k] for k in names if m.get(k,0) == 1]
    empty     = [names[k] for k in names if m.get(k,0) == 0]
    rep = opp.get("영업대표", "담당자")
    viewer = "사업부문장" if viewer_role == "exec" else "영업리더"

    from datetime import date as _date
    today_str = _date.today().strftime("%Y년 %m월 %d일")
    sys_prompt = f"""당신은 SK그룹 영업 AI 어시스턴트입니다.
오늘 날짜: {today_str}
{viewer}이 특정 딜에 대해 물으면, 지금까지 진행된 내용을 **사실 위주로 요약 보고**합니다.

## 답변 원칙 (매우 중요)
- 청자는 {viewer}입니다. 이미 화면에서 담당자·단계를 보고 있으므로, 담당 영업대표 이름을 굳이 언급하지 마세요.
- 영업대표에게 질문하거나 코칭하지 마세요. "~해보셨나요?", "~확인해 주세요", "~에게 지시하세요" 같은 표현 금지.
- 훈수·권고보다 **사실 요약**에 집중하세요: 무엇이 확인됐고, 무엇이 아직 안 됐고, 다음 단계가 무엇인지.
- 자연스러운 보고체로: "~로 확인됩니다", "~까지 진행됐습니다", "~는 아직 파악되지 않았습니다".
- 점수/MEDDPICC 같은 내부 용어 대신 업무 언어로.
- 3~4문장, 군더더기 없이 담백하게.

## 지금까지 입력·확인된 현황
사업기회: {opp.get('사업기회명','')} | 고객사: {opp.get('고객사명','')} | 단계: {opp.get('stage','')}
확인된 항목: {', '.join(confirmed) if confirmed else '없음'}
부분 확인: {', '.join(partial) if partial else '없음'}
미확인 항목: {', '.join(empty) if empty else '없음'}
최근 진행사항: {opp.get('주요_진행사항','')[:200]}
다음 단계: {opp.get('다음_단계','미정')}"""

    return sys_prompt, [{"role": "user", "content": new_message}]

# ══════════════════════════════════════════════
# Fallback 함수 (API 크레딧 소진 시)
# ══════════════════════════════════════════════

DEMO_EVAL_CASE = {
    "금감원": {"I": {"from": 1, "to": 3, "reason": "금감원 정기 검사 데드라인 + 규정상 반드시 구축해야 한다는 명확한 Pain 확인"}},
    "검사": {"I": {"from": 1, "to": 2, "reason": "규정 준수 의무 언급으로 Pain 구체화"}},
    "임원": {"E": {"from": 0, "to": 1, "reason": "임원 직접 관여 언급 (전언이므로 E=1)"}},
    "야근": {"I": {"from": 1, "to": 2, "reason": "야근 언급 = 수작업 과다, 구체적 Pain"}},
    "CIO": {"E": {"from": 1, "to": 2, "reason": "CIO 직접 면담 확인"}},
}

def _fallback_meddpicc_eval(opp: dict, input_text: str) -> dict:
    current = opp.get("meddpicc", {k: 0 for k in ["E","C","M","DC","DP","PP","I","CO"]})
    new_scores = dict(current)
    changes = []

    # 키워드 기반 휴리스틱 평가
    text_lower = input_text

    def _ev(keywords):
        # 매칭된 키워드 주변 문맥을 근거로 추출
        hit = next((w for w in keywords if w in text_lower), None)
        return _extract_evidence(text_lower, hit) if hit else ""

    # ── I: 핵심 Pain ──
    if any(w in text_lower for w in ["금감원", "감사", "검사", "규정상", "감독규정", "감독당국", "가이드라인"]):
        old_i = new_scores.get("I", 0); new_i = min(3, old_i + 2)
        if new_i > old_i:
            changes.append({"item": "I", "from": old_i, "to": new_i, "direction": "up",
                           "reason": "규제/감독당국 대응 압박 — 강한 Pain",
                           "evidence": _ev(["감독규정", "감독당국", "가이드라인", "규정상", "감사"])})
        new_scores["I"] = new_i
    if any(w in text_lower for w in ["오탐", "야근", "수작업", "사후 적발", "사기 패턴", "손실", "민원", "불편", "힘들"]):
        old_i = new_scores.get("I", 0); new_i = min(3, old_i + 2)
        if new_i > old_i:
            changes.append({"item": "I", "from": old_i, "to": new_i, "direction": "up",
                           "reason": "오탐 과다·수작업 부담 등 구체적 운영 Pain",
                           "evidence": _ev(["오탐", "수작업", "사후 적발", "야근"])})
        new_scores["I"] = new_i

    # ── M: 기대 성과 (정량 목표) ──
    if any(w in text_lower for w in ["탐지율", "오탐률", "%p", "% 이상", "절감", "단축", "목표 지표", "기대 효과", "기대 성과", "KPI", "억원 절감", "손실"]):
        old_m = new_scores.get("M", 0)
        # 수치(%, 억원)가 있으면 +2, 방향성만 있으면 +1
        has_metric = any(w in text_lower for w in ["%p", "% 이상", "30%", "20%", "억원"])
        new_m = min(3, old_m + (2 if has_metric else 1))
        if new_m > old_m:
            changes.append({"item": "M", "from": old_m, "to": new_m, "direction": "up",
                           "reason": "정량 목표 지표 확인" if has_metric else "기대 성과 방향성 확인",
                           "evidence": _ev(["20%p", "30% 이상", "억원 절감", "120억", "탐지율 20", "단축"])})
        new_scores["M"] = new_m

    # ── DC: 평가 기준 ──
    if any(w in text_lower for w in ["평가 기준", "평가 항목", "정확도", "재현율", "Recall", "설명가능성", "XAI", "MLOps", "연동", "호환성", "SLA", "선정 시", "중점적으로"]):
        old_dc = new_scores.get("DC", 0); new_dc = min(3, old_dc + 2)
        if new_dc > old_dc:
            changes.append({"item": "DC", "from": old_dc, "to": new_dc, "direction": "up",
                           "reason": "솔루션 선정 평가 기준 명시",
                           "evidence": _ev(["평가 기준", "정확도", "재현율", "설명가능성", "XAI"])})
        new_scores["DC"] = new_dc

    # ── DP: 결정 절차 (일정/프로세스) ──
    if any(w in text_lower for w in ["RFP", "PoC", "평가위원회", "우선협상", "품의", "계약 체결", "추진 절차", "추진 일정", "선정", "발행"]):
        old_dp = new_scores.get("DP", 0)
        # RFP+평가위원회+계약까지 구체 일정이면 +2
        has_timeline = sum(1 for w in ["RFP", "평가위원회", "우선협상", "계약"] if w in text_lower) >= 2
        new_dp = min(3, old_dp + (2 if has_timeline else 1))
        if new_dp > old_dp:
            changes.append({"item": "DP", "from": old_dp, "to": new_dp, "direction": "up",
                           "reason": "RFP·평가·계약 일정 등 결정 절차 가시화" if has_timeline else "결정 절차 일부 확인",
                           "evidence": _ev(["RFP", "평가위원회", "추진 절차", "우선협상"])})
        new_scores["DP"] = new_dp

    # ── E: 예산 집행 권한 ──
    if any(w in text_lower for w in ["전결권", "예산", "편성", "본부장", "상무", "CISO", "CIO", "임원", "전결", "집행"]):
        old_e = new_scores.get("E", 0)
        # 예산 편성 확정 + 전결권자 명시면 +2
        budget_secured = any(w in text_lower for w in ["편성 확정", "예산", "편성"]) and any(w in text_lower for w in ["전결권", "전결", "본부장", "상무"])
        new_e = min(3, old_e + (2 if budget_secured else 1))
        if new_e > old_e:
            changes.append({"item": "E", "from": old_e, "to": new_e, "direction": "up",
                           "reason": "예산 편성 확정 및 전결권자 식별" if budget_secured else "의사결정권자 관여 확인",
                           "evidence": _ev(["전결권", "예산", "편성", "본부장"])})
        new_scores["E"] = new_e

    # ── C: 내부 추진자 ──
    if any(w in text_lower for w in ["추진을 주도", "강하게 주장", "도입 필요성", "적극 협력", "협조", "추진자", "관심", "적극", "밀어", "지지", "우호적"]):
        old_c = new_scores.get("C", 0)
        strong = any(w in text_lower for w in ["추진을 주도", "강하게 주장", "적극 협력"])
        new_c = min(3, old_c + (2 if strong else 1))
        if new_c > old_c:
            changes.append({"item": "C", "from": old_c, "to": new_c, "direction": "up",
                           "reason": "내부 추진을 주도하는 Champion 확인" if strong else "내부 추진자 후보 신호",
                           "evidence": _ev(["추진을 주도", "강하게 주장", "적극 협력", "도입 필요성"])})
        new_scores["C"] = new_c

    # ── CO: 경쟁 현황 ──
    if any(w in text_lower for w in ["경쟁사", "타사", "글로벌 FDS", "핀테크", "자체 개발", "자체구축", "내재화", "A사", "B사"]):
        old_co = new_scores.get("CO", 0); new_co = min(2, old_co + 1)
        if new_co > old_co:
            changes.append({"item": "CO", "from": old_co, "to": new_co, "direction": "up",
                           "reason": "경쟁사·자체개발 등 경쟁 구도 확인",
                           "evidence": _ev(["글로벌 FDS", "핀테크", "자체 개발", "경쟁사", "A사", "B사"])})
        new_scores["CO"] = new_co

    names = {"E": "예산 집행 권한", "I": "핵심 Pain", "C": "내부 추진자",
             "M": "기대 성과", "DC": "평가 기준", "DP": "결정 절차", "CO": "경쟁 현황"}
    # 항목별 근거 표기 전략:
    #  - I, CO: 원문 인용(evidence)이 강한 근거 → evidence 우선
    #  - E, C: 관여/구조 성격 → reason(요약 이유)을 앞세우고 핵심어를 덧붙임
    evidence_first = {"I", "CO", "M", "DC", "DP"}
    preview_parts = []
    for c in changes:
        name = names.get(c["item"], c["item"])
        arrow = "🔺" if c["direction"] == "up" else "🔻"
        ev = (c.get("evidence") or "").strip()
        rs = (c.get("reason") or "").strip()
        if c["item"] in evidence_first:
            basis = ev or rs
        else:
            # E/C: 이유를 앞세움 (관여/구조 성격은 원문 인용보다 요약이 명확)
            basis = rs if rs else ev
        if basis:
            preview_parts.append(f"{arrow} {name}: {basis}")
        else:
            preview_parts.append(f"{arrow} {name} 확인됨")

    preview = " / ".join(preview_parts) if preview_parts else "입력 내용 검토 완료"

    weights = {"E":2.0,"C":2.0,"M":1.5,"DC":1.5,"DP":1.5,"I":1.5,"PP":1.0,"CO":1.0}
    raw = sum(new_scores.get(k,0) * weights[k] for k in weights)
    dq = round((raw/36)*33.3, 2)

    return {
        "scores": new_scores,
        "changes": changes,
        "ambiguities": [],
        "champion_type": None,
        "champion_risk": None,
        "preview_text": preview,
        "next_action": "의사결정권자와 직접 면담 일정을 잡아보세요",
        "deal_quality_score": dq,
    }

def _fallback_chat_reply(opp: dict, message: str, conversation: list) -> str:
    stage = opp.get("stage", "Lead")
    name = opp.get("사업기회명", "이 딜")
    client = opp.get("고객사명", "고객사")

    # ── 부문장/리더의 딜 분석 질의 (회의록 입력이 아닌 질문형) ──
    ANALYTIC = ["리스크", "위험", "어떻게", "왜", "분석", "전략", "강점", "약점",
                "수주", "가능성", "전망", "요약", "현황", "무엇", "뭐", "?", "？"]
    if conversation == [] and any(w in message for w in ANALYTIC):
        m = opp.get("meddpicc", {})
        names = {"E":"예산 권한","C":"내부 추진자","M":"기대 성과","DC":"평가 기준",
                 "DP":"결정 절차","I":"핵심 Pain","CO":"경쟁 현황"}
        empty = [names[k] for k in ["M","E","DC","DP","I","C","CO"] if m.get(k,0)==0]
        strong = [names[k] for k in ["M","E","DC","DP","I","C","CO"] if m.get(k,0)>=2]
        score = opp.get("score", {})
        total = score.get("total", 0) if isinstance(score, dict) else 0
        parts = [f"{client} '{name}' 딜은 현재 {stage} 단계, Deal Score {round(total)}점입니다."]
        if strong:
            parts.append(f"확인된 강점은 {' · '.join(strong[:3])}입니다.")
        if empty:
            tail = empty[min(2, len(empty)-1)]
            josa = "이" if (ord(tail[-1]) - 0xAC00) % 28 != 0 else "가"
            parts.append(f"아직 비어 있는 {' · '.join(empty[:3])}{josa} 가장 큰 리스크 요인입니다. 담당자에게 이 항목 확인을 지시하면 수주 가능성을 높일 수 있습니다.")
        else:
            parts.append("주요 항목이 대부분 확인돼 클로징 단계 점검이 필요합니다.")
        return " ".join(parts)

    if any(w in message for w in ["금감원", "감사", "검사", "규정"]):
        return (f"금감원 검사 데드라인과 임원 직접 관여를 확인했습니다. "
                f"역산하면 8월까지 계약이 돼야 합니다. "
                f"계약 절차를 확인해볼까요? 법무 검토나 이사회 승인 같은 내부 절차가 있는지 여쭤보셨나요?")
    elif any(w in message for w in ["임원", "CIO", "본부장"]):
        return f"의사결정권자가 직접 챙기고 있다니 좋은 신호입니다. 직접 면담이 가능한지 확인해보시겠어요?"
    elif any(w in message for w in ["야근", "힘들", "불편", "수작업"]):
        return f"구체적인 Pain을 확인하셨네요. 혹시 그 문제로 발생하는 비용이나 시간을 수치로 들으셨나요?"
    elif len(conversation) == 0:
        return (f"{client} \"{name}\" 딜 확인해볼게요. "
                f"최근 고객사에서 어떤 이야기가 나왔는지 공유해 주세요.")
    else:
        return (f"말씀해 주신 내용 잘 파악했습니다. "
                f"다음으로 의사결정권자 구조를 확인해보면 좋겠습니다. "
                f"이 사업의 최종 승인을 누가 하는지 파악하셨나요?")

def _fallback_report_reply(opp: dict, message: str, viewer_role: str) -> str:
    """부문장/리더에게 보고하는 폴백 — 진행된 내용을 사실 위주로 요약."""
    m = opp.get("meddpicc", {})
    names = {"E":"예산 집행 권한","C":"내부 추진자","M":"기대 성과","DC":"평가 기준",
             "DP":"결정 절차","PP":"계약 절차","I":"핵심 Pain","CO":"경쟁 현황"}
    confirmed = [names[k] for k in ["I","E","M","DC","DP","C","CO","PP"] if m.get(k,0) >= 2]
    empty     = [names[k] for k in ["M","E","DC","DP","I","C","CO"] if m.get(k,0) == 0]
    name   = opp.get("사업기회명", "이 딜")
    client = opp.get("고객사명", "고객사")
    stage  = opp.get("stage", "")
    progress = (opp.get("주요_진행사항") or "").strip()

    parts = [f"{client} '{name}'는 현재 {stage} 단계입니다."]
    if progress:
        parts.append(f"진행 경과: {progress[:120]}")
    if confirmed:
        parts.append(f"{' · '.join(confirmed[:3])}까지 확인된 상태입니다.")
    if empty:
        tail = empty[min(2, len(empty)-1)]
        josa = "은" if (ord(tail[-1]) - 0xAC00) % 28 != 0 else "는"
        parts.append(f"{' · '.join(empty[:3])}{josa} 아직 파악되지 않았습니다.")
    nxt = opp.get("다음_단계")
    if nxt:
        parts.append(f"다음 단계는 '{nxt}'입니다.")
    return " ".join(parts)

def _fallback_briefing(opps: list, gap: dict, risky: list, urgent: list) -> str:
    risky_names = ", ".join([o.get("사업기회명","") for o in risky[:3]])
    urgent_names = ", ".join([o.get("사업기회명","") for o in urgent[:3]])
    return (
        f"현재 파이프라인 달성률은 {gap['achievement_rate']}%로, "
        f"목표 대비 {gap['gap']:,.0f}억원 Gap이 있습니다. "
        f"위험 딜: {risky_names or '없음'}. "
        f"입찰 임박: {urgent_names or '없음'}. "
        f"Gap 해소를 위해 위험 딜의 Deal Score 개선이 시급합니다."
    )

def _extract_evidence(text: str, keyword: str, context: int = 40) -> str:
    idx = text.find(keyword)
    if idx < 0:
        return keyword
    start = max(0, idx - 15)
    end   = min(len(text), idx + len(keyword) + context)
    snippet = text[start:end].strip()
    # 문장 경계로 다듬기: 앞쪽은 마지막 구두점 이후, 뒤쪽은 첫 구두점까지
    for sep in ["다. ", ". ", "고, ", "는데 ", "고 "]:
        p = snippet.find(sep)
        if 0 <= p < snippet.find(keyword):
            snippet = snippet[p + len(sep):]
            break
    snippet = re.split(r'(?<=다)\.|(?<=요)\.|\. ', snippet)[0].strip()
    # 앞쪽이 단어 중간에서 잘렸으면 첫 공백 이후부터
    if start > 0 and " " in snippet[:20]:
        sp = snippet.find(" ")
        if 0 <= sp < snippet.find(keyword):
            snippet = snippet[sp + 1:]
    if len(snippet) > 60:
        snippet = snippet[:60].rstrip() + "…"
    return snippet

def _fallback_meeting_analyze(opp: dict, meeting_text: str) -> dict:
    NAMES = {"E":"예산 집행 권한","C":"내부 추진자","M":"기대 성과",
             "DC":"평가 기준","DP":"결정 절차","I":"핵심 Pain","CO":"경쟁 현황"}
    # 텍스트 경로와 동일한 정교한 폴백 평가를 재사용 (점수 로직 통일)
    ev = _fallback_meddpicc_eval(opp, meeting_text)
    changes = []
    for c in ev.get("changes", []):
        code = c["item"]
        changes.append({
            "code": code,
            "label": NAMES.get(code, code),
            "from": c["from"], "to": c["to"],
            "evidence": c.get("evidence", ""),
            "reason": c.get("reason", ""),
            "rationale": c.get("reason") or c.get("evidence") or "",
        })

    client = opp.get("고객사명", "고객사")
    if changes:
        # 근거 포함 요약 — 상위 3개 항목을 "항목: 짧은 근거"로 깔끔하게
        def _short(c):
            b = (c.get("evidence") or c.get("reason") or "").strip()
            if len(b) > 28:
                b = b[:28].rstrip() + "…"
            return f"{c['label']}({b})" if b else c['label']
        bullet = ", ".join(_short(c) for c in changes[:3])
        summary = (f"{client} 회의록을 분석했습니다. "
                   f"{bullet} 등 {len(changes)}개 항목에서 근거를 확인했습니다. "
                   f"아래 변경안을 확인하시고 반영 여부를 결정해 주세요.")
    else:
        summary = (f"{client} 회의록을 검토했지만 점수에 반영할 새 단서를 찾지 못했습니다. "
                   f"핵심 내용을 직접 입력해 주셔도 됩니다.")
    return {"summary": summary, "changes": changes}

# ══════════════════════════════════════════════
# API 라우터
# ══════════════════════════════════════════════

# ── 메인 페이지 ──
@app.get("/", response_class=HTMLResponse)
async def root():
    p = BASE / "index.html"
    if p.exists():
        return HTMLResponse(p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>index.html not found</h1>")

@app.get("/landing.html", response_class=HTMLResponse)
async def landing():
    p = BASE / "landing.html"
    if p.exists():
        return HTMLResponse(p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>landing.html not found</h1>")

# ── 데이터 API ──
@app.get("/api/opportunities")
async def get_opportunities(
    dept: Optional[str] = None,
    team: Optional[str] = None,
    rep: Optional[str] = None,
    stage: Optional[str] = None,
    biz_type: Optional[str] = None,
    key_account: Optional[bool] = None,
):
    # ── 캐시 확인 ──
    _t0 = time()
    cache_key = f"opps_{dept or 'all'}_{team or 'all'}_{rep or 'all'}_{stage or 'all'}_{biz_type or 'all'}_{key_account or 'all'}"
    cached = cache_get(cache_key)
    if cached:
        print(f"[timing] GET /api/opportunities cache HIT {(time()-_t0)*1000:.0f}ms key={cache_key}")
        return cached
    print(f"[timing] GET /api/opportunities cache MISS key={cache_key}")

    # 메모리 캐시 우선 사용 — 디스크 I/O + 스코어 재계산 생략
    all_cached = cache_get("opps_all_all_all_all_all_all")
    if all_cached:
        opps = list(all_cached)
    else:
        opps = load("opportunities")
        closed_deals = cache_get("closed_deals_all") or load("closed_deals")
        _rep_idx = _build_closed_index(closed_deals)
        for o in opps:
            o["score"] = calc_deal_score(o, closed_deals, _rep_idx)
            normal = STAGE_NORMAL_DAYS.get(o.get("stage", "Lead"), 30)
            days   = o.get("stage_days", 0)
            o["stage_alert"] = (
                "danger"  if normal > 0 and days > normal * 2   else
                "warning" if normal > 0 and days > normal * 1.5 else
                "normal"
            )

    if dept:        opps = [o for o in opps if o.get("사업부문") == dept]
    if team:        opps = [o for o in opps if o.get("영업팀") == team]
    if rep:         opps = [o for o in opps if o.get("영업대표") == rep]
    if stage:       opps = [o for o in opps if o.get("stage") == stage]
    if biz_type:    opps = [o for o in opps if o.get("사업유형") == biz_type]
    if key_account is not None:
        opps = [o for o in opps if o.get("중점고객여부") == key_account]

    # ── 캐시 저장 ──
    cache_set(cache_key, opps)
    print(f"[timing] GET /api/opportunities DONE {(time()-_t0)*1000:.0f}ms ({len(opps)}건)")
    return opps

@app.get("/api/opportunities/{opp_id}")
async def get_opportunity(opp_id: str):
    cache_key = f"opp_{opp_id}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    # 전체 opps 캐시에서 먼저 찾기 — 파일 읽기 생략
    all_cached = cache_get("opps_all_all_all_all_all_all")
    if all_cached:
        opp = next((o for o in all_cached if o["id"] == opp_id), None)
        if opp:
            cache_set(cache_key, opp)
            return opp
    # fallback: 파일에서 로드
    opps = load("opportunities")
    closed_deals = cache_get("closed_deals_all") or load("closed_deals")
    _rep_idx = _build_closed_index(closed_deals)
    opp = next((o for o in opps if o["id"] == opp_id), None)
    if not opp:
        raise HTTPException(404, "Not found")
    opp["score"] = calc_deal_score(opp, closed_deals, _rep_idx)
    cache_set(cache_key, opp)
    return opp

@app.get("/api/pipeline")
async def get_pipeline(
    dept: Optional[str] = None,
    rep: Optional[str] = None,
):
    # ── 캐시 확인 (A+B 최적화) ──
    _t0 = time()
    cache_key = f"pipeline_{dept or 'all'}_{rep or 'all'}"
    cached = cache_get(cache_key)
    if cached:
        print(f"[timing] GET /api/pipeline cache HIT {(time()-_t0)*1000:.0f}ms key={cache_key}")
        return cached
    print(f"[timing] GET /api/pipeline cache MISS key={cache_key}")

    all_cached = cache_get("opps_all_all_all_all_all_all")
    if all_cached:
        opps = list(all_cached)
        closed_deals = None  # 스코어 이미 계산됨
    else:
        opps = load("opportunities")
        closed_deals = cache_get("closed_deals_all") or load("closed_deals")
    if dept: opps = [o for o in opps if o.get("사업부문") == dept]
    if rep:  opps = [o for o in opps if o.get("영업대표") == rep]

    gap  = calc_pipeline_gap(opps)
    reps_list = load("reps")

    # Waterfall 데이터
    STAGES = ["Lead","Identified","Identified-Remind","Identified-Registered",
              "Validated","Qualified","Negotiated","우선협상","계약완료"]
    waterfall = []
    for s in STAGES:
        s_opps = [o for o in opps if o["stage"] == s]
        waterfall.append({
            "stage": s, "count": len(s_opps),
            "total": sum(o["수주목표액"] for o in s_opps),
            "weighted": round(sum(o["수주목표액"] * o.get("수주확도",0)/100 for o in s_opps), 1),
        })

    # 팀원별 현황 — opp["score"] 또는 개별 캐시 우선 사용
    def _get_score(o):
        if o.get("score", {}).get("total"):
            return o["score"]["total"]
        cached_opp = cache_get(f"opp_{o['id']}")
        if cached_opp and cached_opp.get("score"):
            return cached_opp["score"]["total"]
        if closed_deals is not None:
            return calc_deal_score(o, closed_deals)["total"]
        return 0

    rep_summary = []
    all_reps = list(set(o.get("영업대표","") for o in opps))
    for r in all_reps:
        r_opps = [o for o in opps if o.get("영업대표") == r]
        scores = [_get_score(o) for o in r_opps]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0
        rep_summary.append({
            "name": r, "deal_count": len(r_opps),
            "total_amount": sum(o["수주목표액"] for o in r_opps),
            "avg_score": avg_score,
        })

    # 팀 평균 스코어 — 동일하게 캐시 재활용
    all_scores = [_get_score(o) for o in opps]
    team_avg   = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
    for r in rep_summary:
        r["score_alert"] = r["avg_score"] < team_avg - 15

    result = {"gap": gap, "waterfall": waterfall, "rep_summary": rep_summary, "team_avg_score": team_avg}

    # ── 캐시 저장 (5분) ──
    cache_set(cache_key, result)
    print(f"[timing] GET /api/pipeline DONE {(time()-_t0)*1000:.0f}ms")

    return result

@app.get("/api/reps")
async def get_reps():
    return load("reps")

@app.get("/api/accounts")
async def get_accounts():
    return load("accounts")

@app.get("/api/history")
async def get_history(rep: Optional[str] = None, account: Optional[str] = None):
    hist = load("history")
    if rep:     hist = [h for h in hist if h.get("영업대표") == rep]
    if account: hist = [h for h in hist if h.get("고객사명") == account]
    # 담당자 승률 계산
    if rep:
        closed = [h for h in hist if h.get("결과") in ("계약완료","Deal Lost")]
        won    = [h for h in closed if h.get("결과") == "계약완료"]
        return {
            "history": hist,
            "win_rate": round(len(won) / len(closed) * 100, 1) if closed else 0,
            "total": len(closed), "won": len(won),
        }
    return hist

# ── Agent 대화 API ──
class ChatRequest(BaseModel):
    opp_id: str
    message: str
    conversation: list = []
    role: str = "rep"  # "rep"(영업대표 코칭) | "exec"/"leader"(보고형)

@app.post("/api/chat")
async def chat(req: ChatRequest):
    opps = load("opportunities")
    opp  = next((o for o in opps if o["id"] == req.opp_id), None)
    if not opp:
        raise HTTPException(404, "Opportunity not found")

    is_report = req.role in ("exec", "leader")

    if not ANTHROPIC_OK:
        if is_report:
            return {"reply": _fallback_report_reply(opp, req.message, req.role), "meddpicc_eval": None}
        return {
            "reply": _fallback_chat_reply(opp, req.message, req.conversation),
            "meddpicc_eval": None,
        }

    if _is_demo_opp(req.opp_id) and not is_report and _is_demo_step3(req.message):
        return {"reply": _DEMO_CHAT_3, "meddpicc_eval": None}

    if is_report:
        sys_prompt, messages = build_report_chat_prompt(opp, req.message, req.role)
    else:
        sys_prompt, messages = build_agent_chat_prompt(opp, req.conversation, req.message)

    try:
        resp = _client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            temperature=0,
            system=sys_prompt,
            messages=messages,
        )
        reply = resp.content[0].text
    except Exception as e:
        reply = _fallback_report_reply(opp, req.message, req.role) if is_report \
                else _fallback_chat_reply(opp, req.message, req.conversation)
    return {"reply": reply, "meddpicc_eval": None}

# ── MEDDPICC 평가 API ──
class EvalRequest(BaseModel):
    opp_id: str
    input_text: str
    conversation: list = []

@app.post("/api/meddpicc/evaluate")
async def evaluate_meddpicc(req: EvalRequest):
    opps    = load("opportunities")
    opp     = next((o for o in opps if o["id"] == req.opp_id), None)
    if not opp:
        raise HTTPException(404, "Opportunity not found")

    if _is_demo_opp(req.opp_id):
        if _is_demo_doc2(req.input_text):
            return _DEMO_FIXTURE_2
        if _is_demo_step3(req.input_text):
            return _DEMO_FIXTURE_3

    fewshots = load_fewshots()
    sys_prompt, user_prompt = build_meddpicc_prompt(opp, req.input_text, fewshots, req.conversation)

    if not ANTHROPIC_OK:
        # API 키 미설정 시 키워드 기반 폴백 평가 (시연/오프라인 동작 보장)
        return _fallback_meddpicc_eval(opp, req.input_text)

    try:
        resp = _client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            temperature=0,
            system=sys_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = resp.content[0].text.strip()
        try:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            result = json.loads(m.group() if m else raw)
        except Exception:
            result = {"scores": opp.get("meddpicc", {}), "changes": [],
                      "ambiguities": [], "preview_text": raw, "next_action": ""}
    except Exception:
        result = _fallback_meddpicc_eval(opp, req.input_text)
    return result

# ── MEDDPICC 업데이트 API (HITL 확정) ──
class UpdateRequest(BaseModel):
    opp_id: str
    scores: dict
    input_text: str
    llm_scores: dict
    preview_text: str = ""
    conversation: list = []
    new_stage: str = ""   # 추천 stage 변경 (빈 문자열이면 유지)

@app.post("/api/meddpicc/update")
async def update_meddpicc(req: UpdateRequest):
    opps    = load("opportunities")
    idx     = next((i for i,o in enumerate(opps) if o["id"] == req.opp_id), None)
    if idx is None:
        raise HTTPException(404, "Not found")

    opp = opps[idx]
    old_scores = opp.get("meddpicc", {})

    # 충돌 감지: LLM 점수 vs 최종 반영 점수
    conflicts = load("conflict_log")
    for item in ["E","C","M","DC","DP","PP","I","CO"]:
        llm_s   = req.llm_scores.get(item, old_scores.get(item, 0))
        final_s = req.scores.get(item, old_scores.get(item, 0))
        if llm_s != final_s:
            conflict_id = f"CONF-{len(conflicts)+1:03d}"
            conflicts.append({
                "id": conflict_id,
                "timestamp": datetime.now().isoformat(),
                "deal_id": req.opp_id,
                "deal_name": opp.get("사업기회명", ""),
                "stage": opp.get("stage", ""),
                "rep_name": opp.get("영업대표", ""),
                "item": item,
                "trigger_text": req.input_text[:80],
                "full_input": req.input_text,
                "llm_score": llm_s,
                "rep_final_score": final_s,
                "llm_reason": f"LLM이 {item}={llm_s}으로 평가",
                "rep_correction": f"영업대표가 {final_s}으로 수정",
                "status": "pending",
                "admin_note": "",
                "fewshot_id": None,
            })

    # 업데이트 적용
    opps[idx]["meddpicc"] = req.scores
    # Stage 변경
    VALID_STAGES = {"Lead","Identified","Validated","Qualified","Negotiated","우선협상","계약완료","Deselected"}
    old_stage = opps[idx].get("stage", "")
    stage_changed = False
    if req.new_stage and req.new_stage in VALID_STAGES and req.new_stage != old_stage:
        opps[idx]["stage"] = req.new_stage
        opps[idx]["stage_days"] = 0
        stage_changed = True
    # 진행사항 업데이트 — 변경된 항목을 간결히 누적 (긴 요약 전체는 넣지 않음)
    _names = {"E": "예산 집행 권한", "C": "내부 추진자", "M": "기대 성과",
              "DC": "평가 기준", "DP": "결정 절차", "PP": "계약 절차",
              "I": "핵심 Pain", "CO": "경쟁 현황"}
    _changed = ", ".join(
        f"{_names.get(code, code)} {old_scores.get(code,0)}→{req.scores.get(code, old_scores.get(code,0))}"
        for code in ["E","C","M","DC","DP","PP","I","CO"]
        if req.scores.get(code, old_scores.get(code,0)) != old_scores.get(code,0)
    )
    if _changed:
        existing = opps[idx].get("주요_진행사항", "")
        today    = date.today().strftime("%Y-%m-%d")
        opps[idx]["주요_진행사항"] = f"{existing}\n[{today}] {_changed}".strip()
    elif req.preview_text:
        existing = opps[idx].get("주요_진행사항", "")
        today    = date.today().strftime("%Y-%m-%d")
        opps[idx]["주요_진행사항"] = f"{existing}\n[{today}] {req.preview_text}".strip()
    opps[idx]["최근_업데이트일"] = date.today().strftime("%Y-%m-%d")

    save("opportunities", opps)
    save("conflict_log", conflicts)
    cache_clear(req.opp_id)

    # ── 첨부·활동 로그 적재 (별도 activity_log 파일) ──
    raw_in = req.input_text or ""
    fname = ""
    m = re.match(r"\[회의록 분석\]\s*(.+)", raw_in)
    if m:
        fname = m.group(1).strip()
        source = "file"
    else:
        source = "text"

    change_chips = []
    code_names = {"E": "예산 집행 권한", "C": "내부 추진자", "M": "기대 성과",
                  "DC": "평가 기준", "DP": "결정 절차", "PP": "계약 절차",
                  "I": "핵심 Pain", "CO": "경쟁 현황"}
    for code in ["E", "C", "M", "DC", "DP", "PP", "I", "CO"]:
        old_v = old_scores.get(code, 0)
        new_v = req.scores.get(code, old_v)
        if new_v != old_v:
            change_chips.append({
                "code": code,
                "name": code_names.get(code, code),
                "from": old_v, "to": new_v,
            })

    # ── AI 대화 요약 생성 ──
    ai_title = ""
    ai_summary = ""
    conv = req.conversation or []
    changed_names = [f"{c['name']}({c['code']})" for c in change_chips]
    changed_str = ", ".join(changed_names) if changed_names else "없음"
    score_change_lines = "\n".join(
        f"- {c['name']}({c['code']}): {c['from']}점 → {c['to']}점"
        for c in change_chips
    ) if change_chips else "- 변경 없음"

    # 입력 소스 구성 (대화 or 붙여넣은 텍스트 or 파일)
    conv_text = "\n".join(
        f"{'영업대표' if t.get('role')=='rep' else 'AI'}: {t.get('text','')[:300]}"
        for t in conv[-20:]
    ) if conv else ""
    raw_input = req.input_text or ""
    source_text = conv_text if conv_text else raw_input[:1500]

    if source_text and ANTHROPIC_OK:
        try:
            sum_prompt = f"""영업대표가 딜 관련 정보를 입력했습니다. 아래 내용을 바탕으로 활동 이력을 작성하세요.

딜명: {opp.get('사업기회명','')} / 고객사: {opp.get('고객사명','')}
입력 내용:
{source_text}

이번 업데이트로 변경된 MEDDPICC 점수:
{score_change_lines}

아래 JSON 형식으로만 답하세요:
{{
  "title": "날짜없이 핵심만 (예: CTO 통화 — 결정절차·챔피언 확인) 20자 이내",
  "summary": "- 파악된 핵심사실 1\n- 파악된 핵심사실 2\n- 파악된 핵심사실 3\n[스코어 변경] {changed_str}"
}}"""
            resp = _client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=400,
                messages=[{"role": "user", "content": sum_prompt}]
            )
            import json as _json
            parsed = _json.loads(resp.content[0].text.strip())
            ai_title = parsed.get("title", "")
            ai_summary = parsed.get("summary", "")
        except Exception:
            pass

    if not ai_title:
        ai_title = fname if fname else ("대화 업데이트" if conv else "메모 입력")
    if not ai_summary:
        ai_summary = score_change_lines

    activity = load("activity_log")
    if not isinstance(activity, list):
        activity = []
    activity_entry = {
        "id": f"ACT-{len(activity)+1:04d}",
        "timestamp": datetime.now().isoformat(),
        "opp_id": req.opp_id,
        "deal_name": opp.get("사업기회명", ""),
        "source": source,
        "filename": fname,
        "title": ai_title,
        "summary": ai_summary,
        "changes": change_chips,
    }
    activity.append(activity_entry)
    save("activity_log", activity)

    # 활동성 점수 계산에 쓰이는 embedded activity_log도 동기화
    today_str = datetime.now().strftime("%Y-%m-%d")
    if "activity_log" not in opps[idx] or not isinstance(opps[idx]["activity_log"], list):
        opps[idx]["activity_log"] = []
    opps[idx]["activity_log"].append({"date": today_str, "type": "meddpicc_update"})
    save("opportunities", opps)
    # 업데이트된 딜 즉시 재캐싱 (파이프라인 캐시만 무효화)
    closed_deals = load("closed_deals")
    opps[idx]["score"] = calc_deal_score(opps[idx], closed_deals)
    cache_set(f"opp_{req.opp_id}", opps[idx])
    for k in [k for k in _cache if k.startswith("pipeline_") or k.startswith("opps_")]:
        _cache.pop(k, None)
    return {"success": True, "score": opps[idx]["score"]}

# ── 회의록 분석 API ──
class MeetingAnalyzeRequest(BaseModel):
    opp_id: str
    meeting_text: str

@app.post("/api/meeting/analyze")
async def analyze_meeting(req: MeetingAnalyzeRequest):
    opps = load("opportunities")
    opp  = next((o for o in opps if o["id"] == req.opp_id), None)
    if not opp:
        raise HTTPException(404, "Opportunity not found")

    if _is_demo_opp(req.opp_id) and _is_demo_doc1(req.meeting_text):
        return _DEMO_FIXTURE_1

    if not ANTHROPIC_OK:
        return _fallback_meeting_analyze(opp, req.meeting_text)

    codes = ["E","C","M","DC","DP","I","CO"]
    cur   = opp.get("meddpicc", {})
    scores_str = json.dumps({k: cur.get(k, 0) for k in codes}, ensure_ascii=False)

    system_prompt = (
        "당신은 B2B 영업 딜을 평가하는 어시스턴트입니다. 아래 회의록만 근거로\n"
        "MEDDPICC 7개 항목을 평가하세요: E(예산집행권한) C(내부추진자) M(기대성과)\n"
        "DC(평가기준) DP(결정절차) I(핵심Pain) CO(경쟁현황).\n"
        "규칙:\n"
        "- 점수는 0~3. 회의록에 명확한 근거 문구가 있을 때만 올립니다.\n"
        "- 근거를 원문 그대로 evidence에 인용할 수 없으면 점수를 올리지 마세요(환각 금지).\n"
        "- 추측·일반론으로 올리지 마세요.\n"
        "- summary는 영업대표에게 말하듯 자연스러운 한 문단:\n"
        "  회의 날짜·상대 + 핵심 발견(인용 포함) + 다음 액션 제안 1개.\n"
        "- 아래 JSON만 출력. 다른 텍스트·마크다운·코드펜스 금지."
    )
    user_prompt = f'현재 점수: {scores_str}\n회의록:\n"""{req.meeting_text}"""'

    try:
        resp = _client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```\s*$', '', raw, flags=re.MULTILINE)
        result = json.loads(raw)
        if "summary" not in result or "changes" not in result:
            raise ValueError("schema mismatch")
        result["changes"] = [c for c in result["changes"] if str(c.get("evidence", "")).strip()]
        return result
    except Exception:
        return _fallback_meeting_analyze(opp, req.meeting_text)


# ── 회의록 컨텍스트 기반 후속 질문 API ──
class MeetingChatRequest(BaseModel):
    opp_id: str
    meeting_text: str
    question: str

@app.post("/api/meeting/chat")
async def meeting_chat(req: MeetingChatRequest):
    from datetime import date as _date
    today_str = _date.today().strftime("%Y년 %m월 %d일")
    system_prompt = (
        f"오늘 날짜: {today_str}\n"
        "아래 회의록 내용만 근거로 사용자 질문에 한국어로 답하세요.\n"
        "회의록에 없는 내용은 \"회의록에 명시되어 있지 않습니다\"라고 답하고 지어내지 마세요.\n"
        "날짜 언급 시 오늘 날짜 기준으로 '며칠 전', '며칠 후' 등 정확히 표현하세요.\n"
        "답변 후, 아직 채워지지 않은 MEDDPICC 항목이 있고 자연스러울 때만\n"
        "보완 질문 1개를 덧붙이세요(없으면 생략)."
    )
    user_prompt = f'회의록:\n"""{req.meeting_text}"""\n\n질문: {req.question}'

    if not ANTHROPIC_OK:
        # 키워드 기반 폴백 — 크레딧 없이도 회의록 근거로 자연스럽게 답변
        q = req.question
        text = req.meeting_text

        def _find(kws):
            return [w for w in kws if w in text]

        topic_map = {
            "Pain": ["오탐", "미탐", "야근", "수작업", "한계", "불편", "사후"],
            "성과": ["향상", "절감", "단축", "%", "억원", "목표", "KPI"],
            "예산": ["예산", "전결", "본부장", "상무", "CISO", "편성"],
            "경쟁": ["경쟁", "A사", "B사", "자체", "타사", "핀테크"],
            "일정": ["RFP", "PoC", "평가위원회", "계약", "월"],
        }
        hit_topic = next((t for t, kws in topic_map.items()
                          if any(k in q for k in [t]) or _find(kws)), None)

        if hit_topic:
            found = _find(topic_map[hit_topic])
            if found:
                ctx = next((line.strip() for line in text.split('.')
                            if any(f in line for f in found)), "")
                reply = (f"회의록 근거로 보면 — {ctx.strip()[:80]}…\n"
                         f"이 내용이 {hit_topic} 관련 핵심입니다. "
                         f"추가로 구체적 수치나 담당자를 확인하시면 점수가 더 정교해집니다.")
            else:
                reply = f"회의록에는 {hit_topic} 관련 직접 언급이 보이지 않습니다. 다음 미팅에서 확인을 권합니다."
        else:
            reply = ("회의록 내용을 기준으로 답변드렸습니다. "
                     "구체적인 수치나 의사결정 구조를 더 말씀해 주시면 점수에 반영하겠습니다.")
        return {"reply": reply}

    # meeting_text가 너무 길면 앞 3000자만 사용
    if len(req.meeting_text) > 3000:
        truncated = req.meeting_text[:3000] + "\n...(이하 생략)"
        user_prompt = f'회의록:\n"""{truncated}"""\n\n질문: {req.question}'

    try:
        resp = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        reply = resp.content[0].text
    except Exception as e:
        import traceback; traceback.print_exc()
        reply = "답변 생성 중 오류가 발생했습니다. 다시 시도해 주세요."

    return {"reply": reply}


# ── AI 브리핑 API (사업부문장/영업리더용) ──
class BriefingRequest(BaseModel):
    role: str  # "leader" | "exec"
    rep_name: Optional[str] = None
    dept: Optional[str] = None
    query: Optional[str] = None

@app.post("/api/briefing")
async def get_briefing(req: BriefingRequest):
    _t0 = time()
    # ── 캐시 확인 (쿼리가 없을 때만) ──
    cache_key = f"briefing_{req.role}_{req.dept or 'all'}_{req.rep_name or 'all'}"
    if not req.query:  # 사용자 질의가 없을 때만 캐시
        cached = cache_get(cache_key)
        if cached:
            print(f"[timing] POST /api/briefing cache HIT {(time()-_t0)*1000:.0f}ms key={cache_key}")
            return cached
    print(f"[timing] POST /api/briefing cache MISS key={cache_key}")

    # 메모리 캐시 우선 — 디스크 I/O 생략
    all_cached = cache_get("opps_all_all_all_all_all_all")
    if all_cached:
        opps = list(all_cached)
    else:
        opps = load("opportunities")
        closed_deals = load("closed_deals")
        for o in opps:
            o["score"] = calc_deal_score(o, closed_deals)
    if req.dept:     opps = [o for o in opps if o.get("사업부문") == req.dept]
    if req.rep_name: opps = [o for o in opps if o.get("영업대표") == req.rep_name]

    for o in opps:
        _sc = o.get("score") or (cache_get(f"opp_{o['id']}") or {}).get("score") or {"total": 0}
        o["_score"] = _sc

    # 위험 딜 선별
    risky = sorted(
        [o for o in opps if o["_score"]["total"] < 40 or o.get("stage_alert") == "danger"],
        key=lambda x: x["_score"]["total"]
    )[:5]

    # 입찰 임박 (30일 이내)
    today = date.today()
    urgent = []
    for o in opps:
        bid = o.get("입찰일", "")
        if bid:
            try:
                days_to_bid = (datetime.strptime(bid, "%Y-%m-%d").date() - today).days
                if 0 <= days_to_bid <= 30:
                    o["_days_to_bid"] = days_to_bid
                    urgent.append(o)
            except Exception:
                pass
    urgent.sort(key=lambda x: x.get("_days_to_bid", 99))

    if not ANTHROPIC_OK:
        result = {
            "briefing": "API 키 미설정 상태입니다.",
            "risky_deals": risky[:3],
            "urgent_deals": urgent[:3],
            "gap": calc_pipeline_gap(opps),
        }
        if not req.query:
            cache_set(cache_key, result)
        return result

    # 브리핑용 컨텍스트
    gap    = calc_pipeline_gap(opps)
    risky_summary = "\n".join([
        f"- {o['사업기회명']} ({o['영업대표']}, Score:{o['_score']['total']}점, {o['stage']})"
        for o in risky[:3]
    ])
    urgent_summary = "\n".join([
        f"- {o['사업기회명']} (입찰 D-{o.get('_days_to_bid','?')}, {o['영업대표']})"
        for o in urgent[:5]
    ])

    role_ctx = (
        "영업리더 (팀 단위 관리, 팀원별 딜 현황 중심)"
        if req.role == "leader" else
        "사업부문장 (전체 파이프라인, Gap 관리, 전략적 판단 중심)"
    )

    user_q = req.query or "오늘 파이프라인 현황을 브리핑해주세요."

    sys_p = f"""당신은 SK그룹 영업 AI 어시스턴트입니다.
{role_ctx}에게 파이프라인 현황을 브리핑합니다.
간결하고 실행 가능한 인사이트를 제공하세요. 3~5문장으로 요약합니다."""

    user_p = f"""파이프라인 현황:
- 총 {len(opps)}건 / {sum(o['수주목표액'] for o in opps):,}억
- 수주 Gap: {gap['gap']:,}억 (달성률 {gap['achievement_rate']}%)
- 위험 딜 ({len(risky)}건): {risky_summary or '없음'}
- 입찰 임박 ({len(urgent)}건): {urgent_summary or '없음'}

질문: {user_q}"""

    _t_ai = time()
    try:
        resp = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            temperature=0,
            system=sys_p,
            messages=[{"role": "user", "content": user_p}],
        )
        briefing_text = resp.content[0].text
        print(f"[timing] briefing AI call {(time()-_t_ai)*1000:.0f}ms")
    except Exception:
        briefing_text = _fallback_briefing(opps, gap, risky, urgent)

    result = {
        "briefing": briefing_text,
        "risky_deals": risky[:3],
        "urgent_deals": urgent[:5],
        "gap": gap,
    }

    # ── 캐시 저장 (사용자 쿼리가 없을 때만) ──
    if not req.query:
        cache_set(cache_key, result)

    print(f"[timing] POST /api/briefing DONE {(time()-_t0)*1000:.0f}ms")
    return result

# ── 관리자 API ──
@app.get("/api/admin/conflicts")
async def get_conflicts(status: Optional[str] = None, item: Optional[str] = None):
    cache_key = f"admin_conflicts_{status or 'all'}_{item or 'all'}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    conflicts = load("conflict_log")
    if status: conflicts = [c for c in conflicts if c.get("status") == status]
    if item:   conflicts = [c for c in conflicts if c.get("item") == item]
    conflicts.sort(key=lambda x: x.get("timestamp",""), reverse=True)
    cache_set(cache_key, conflicts)
    return conflicts

class ConflictUpdate(BaseModel):
    correct_score: int
    admin_note: str = ""
    add_to_fewshot: bool = False

@app.post("/api/admin/conflicts/{conflict_id}")
async def update_conflict(conflict_id: str, req: ConflictUpdate):
    conflicts = load("conflict_log")
    idx = next((i for i,c in enumerate(conflicts) if c["id"] == conflict_id), None)
    if idx is None:
        raise HTTPException(404, "Conflict not found")

    conflicts[idx]["admin_note"]    = req.admin_note
    conflicts[idx]["correct_score"] = req.correct_score

    if req.add_to_fewshot:
        conflicts[idx]["status"] = "added_to_fewshot"
        fewshots = load_fewshots()
        new_fs = {
            "id": f"FS-{conflicts[idx]['item']}-{len(fewshots)+1:02d}",
            "item": conflicts[idx]["item"],
            "input": conflicts[idx]["full_input"],
            "correct_score": req.correct_score,
            "wrong_score": conflicts[idx]["llm_score"],
            "reason": req.admin_note,
            "created_by": "관리자",
            "created_at": datetime.now().isoformat(),
            "source_conflict_id": conflict_id,
        }
        conflicts[idx]["fewshot_id"] = new_fs["id"]
        fewshots.append(new_fs)
        save_fewshots(fewshots)
    else:
        conflicts[idx]["status"] = "dismissed"

    save("conflict_log", conflicts)
    for k in [k for k in _cache if k.startswith("admin_conflicts")]:
        _cache.pop(k, None)
    if req.add_to_fewshot:
        _cache.pop("admin_fewshots", None)
    return {"success": True}

@app.get("/api/admin/fewshots")
async def get_fewshots():
    cached = cache_get("admin_fewshots")
    if cached is not None:
        return cached
    result = load_fewshots()
    cache_set("admin_fewshots", result)
    return result

class FewshotCreate(BaseModel):
    item: str
    input: str
    correct_score: int
    wrong_score: int
    reason: str

@app.post("/api/admin/fewshots")
async def create_fewshot(req: FewshotCreate):
    fewshots = load_fewshots()
    new_id   = f"FS-{req.item}-{len(fewshots)+1:02d}"
    fewshots.append({
        "id": new_id, "item": req.item, "input": req.input,
        "correct_score": req.correct_score, "wrong_score": req.wrong_score,
        "reason": req.reason, "created_by": "관리자",
        "created_at": datetime.now().isoformat(), "source_conflict_id": None,
    })
    save_fewshots(fewshots)
    _cache.pop("admin_fewshots", None)
    return {"success": True, "id": new_id}

@app.delete("/api/admin/fewshots/{fs_id}")
async def delete_fewshot(fs_id: str):
    fewshots = load_fewshots()
    fewshots = [f for f in fewshots if f["id"] != fs_id]
    save_fewshots(fewshots)
    _cache.pop("admin_fewshots", None)
    return {"success": True}

@app.get("/api/admin/prompt")
async def get_system_prompt():
    cached = cache_get("admin_prompt")
    if cached is not None:
        return cached
    result = {"prompt": load_prompt()}
    cache_set("admin_prompt", result)
    return result

class PromptUpdate(BaseModel):
    prompt: str

@app.put("/api/admin/prompt")
async def update_system_prompt(req: PromptUpdate):
    save_prompt(req.prompt)
    _cache.pop("admin_prompt", None)
    return {"success": True}

# ── 아젠다 Push API ──
class AgendaRequest(BaseModel):
    opp_id: str
    agenda: str
    target_rep: str
    exec_name: str = "전영업"

@app.post("/api/agenda")
async def push_agenda(req: AgendaRequest):
    opps = load("opportunities")
    opp  = next((o for o in opps if o["id"] == req.opp_id), None)
    if not opp:
        raise HTTPException(404, "Not found")

    # 실제 Push 알림은 MVP에서 로그로 대체
    agendas = load("agenda_log") if (DATA / "agenda_log.json").exists() else []
    agendas.append({
        "id": f"AGN-{len(agendas)+1:03d}",
        "timestamp": datetime.now().isoformat(),
        "deal_id": req.opp_id,
        "deal_name": opp.get("사업기회명",""),
        "agenda": req.agenda,
        "target_rep": req.target_rep,
        "pushed_by": req.exec_name,
        "status": "sent",
    })
    save("agenda_log", agendas)
    return {"success": True, "message": f"{req.target_rep}에게 아젠다가 전달됐습니다."}

@app.get("/api/agenda")
async def get_agendas(rep: Optional[str] = None):
    agendas = load("agenda_log") if (DATA / "agenda_log.json").exists() else []
    if rep: agendas = [a for a in agendas if a.get("target_rep") == rep]
    return agendas

# ── 코칭 메모 API ──
class CoachingNoteRequest(BaseModel):
    opp_id: str
    content: str
    author: str = ""
    role: str = "leader"

@app.post("/api/coaching-notes")
async def save_coaching_note(req: CoachingNoteRequest):
    # 캐시된 전체 opps 사용 — 디스크 읽기 생략
    all_cached = cache_get("opps_all_all_all_all_all_all")
    opps = list(all_cached) if all_cached else load("opportunities")

    idx = next((i for i,o in enumerate(opps) if o["id"] == req.opp_id), None)
    if idx is None:
        raise HTTPException(404, "Opportunity not found")

    opp = opps[idx]
    if "coaching_notes" not in opp:
        opp["coaching_notes"] = []

    note = {
        "timestamp": datetime.now().isoformat(),
        "author": req.author,
        "role": req.role,
        "content": req.content
    }
    opp["coaching_notes"].append(note)
    opp["최근_업데이트일"] = date.today().strftime("%Y-%m-%d")

    save("opportunities", opps)
    # 캐시 갱신
    cache_set("opps_all_all_all_all_all_all", opps)
    cache_set(f"opp_{opp['id']}", opp)
    return {"success": True, "note": note}

@app.get("/api/coaching-notes/{opp_id}")
async def get_coaching_notes(opp_id: str):
    opps = load("opportunities")
    opp = next((o for o in opps if o["id"] == opp_id), None)
    if not opp:
        raise HTTPException(404, "Opportunity not found")
    return {"coaching_notes": opp.get("coaching_notes", [])}

# ── 첨부·활동 로그 조회 ──
@app.get("/api/meeting/log/{opp_id}")
async def get_meeting_log(opp_id: str):
    activity = load("activity_log")
    if not isinstance(activity, list):
        activity = []
    items = [a for a in activity if a.get("opp_id") == opp_id]
    # 최신순 정렬
    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {"opp_id": opp_id, "count": len(items), "items": items}

# ── 헬스체크 ──
# ── MEDDPICC 가중치 API ──
@app.get("/api/admin/meddpicc-weights")
async def get_meddpicc_weights():
    return load_meddpicc_weights()

class WeightsUpdate(BaseModel):
    weights: dict

@app.put("/api/admin/meddpicc-weights")
async def update_meddpicc_weights(req: WeightsUpdate):
    keys = set(_DEFAULT_WEIGHTS.keys())
    for k in req.weights:
        if k not in keys:
            raise HTTPException(400, f"Unknown item: {k}")
        v = req.weights[k]
        if not (0.0 <= v <= 5.0):
            raise HTTPException(400, f"Weight out of range (0~5): {k}={v}")
    save_meddpicc_weights(req.weights)
    invalidate_weights_cache()
    _cache.pop("admin_meddpicc_analysis", None)
    cache_clear()  # 가중치 변경 시 스코어 캐시 무효화
    return {"success": True}

# ── MEDDPICC 수주 상관관계 분석 API ──
@app.get("/api/admin/meddpicc-analysis")
async def meddpicc_analysis():
    cached = cache_get("admin_meddpicc_analysis")
    if cached is not None:
        return cached
    opps = load("opportunities")
    ITEMS = list(_DEFAULT_WEIGHTS.keys())
    NAMES = {
        "E":"예산 집행 권한","C":"내부 추진자","M":"기대 성과",
        "DC":"평가 기준","DP":"결정 절차","PP":"계약 절차",
        "I":"핵심 Pain","CO":"경쟁 현황",
    }

    won_opps  = [o for o in opps if o.get("stage") == "계약완료"]
    active    = [o for o in opps if o.get("stage") not in ("계약완료",)]

    def avg_scores(deal_list):
        if not deal_list:
            return {k: 0.0 for k in ITEMS}
        result = {}
        for k in ITEMS:
            vals = [o.get("meddpicc", {}).get(k, 0) for o in deal_list]
            result[k] = round(sum(vals) / len(vals), 2)
        return result

    won_avg    = avg_scores(won_opps)
    active_avg = avg_scores(active)

    # 항목별 분포 (0점/1점/2점/3점 각각 몇 건인지)
    def score_dist(deal_list, key):
        dist = {0: 0, 1: 0, 2: 0, 3: 0}
        for o in deal_list:
            s = o.get("meddpicc", {}).get(key, 0)
            dist[min(s, 3)] += 1
        return dist

    items_detail = []
    current_weights = load_meddpicc_weights()
    for k in ITEMS:
        items_detail.append({
            "code": k,
            "name": NAMES[k],
            "weight": current_weights.get(k, _DEFAULT_WEIGHTS[k]),
            "won_avg": won_avg[k],
            "active_avg": active_avg[k],
            # 수주 딜에서 이 항목이 높을수록 수주에 기여했다고 볼 수 있는 지표
            "won_dist": score_dist(won_opps, k),
            "won_high_rate": round(
                sum(1 for o in won_opps if o.get("meddpicc",{}).get(k,0) >= 2)
                / len(won_opps) * 100, 1
            ) if won_opps else 0,
        })

    result = {
        "won_count": len(won_opps),
        "active_count": len(active),
        "items": items_detail,
    }
    cache_set("admin_meddpicc_analysis", result)
    return result

@app.post("/api/admin/reset")
async def admin_reset():
    seed_dir = BASE / "data_seed"
    if not seed_dir.exists():
        raise HTTPException(500, "data_seed 폴더를 찾을 수 없습니다.")
    import shutil
    for seed_file in seed_dir.glob("*.json"):
        shutil.copy2(seed_file, DATA / seed_file.name)
    return {"status": "ok", "message": "데이터가 초기화됐습니다."}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "anthropic": ANTHROPIC_OK,
        "data_files": {
            name: (DATA / f"{name}.json").exists()
            for name in ["opportunities","history","reps","accounts","conflict_log","conversation_log"]
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
