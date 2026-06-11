# -*- coding: utf-8 -*-
"""
시연 리셋 스크립트
하나은행 이상거래탐지 딜(OPP-2026-124)을 '회의록 첨부 전' 상태(44점)로 되돌리고
첨부·활동 로그(conversation_log.json)를 비웁니다.

사용법 (서버가 떠 있어도 됨, 새 터미널에서):
    python reset_demo.py
실행 후 브라우저에서 Ctrl+Shift+R (강력 새로고침)
"""
import json
from pathlib import Path

DATA = Path(__file__).parent / "data"

# ── 이 딜만 초기 상태로 ──
TARGET_ID = "OPP-2026-124"
INITIAL_MEDDPICC = {"E": 0, "C": 1, "M": 0, "DC": 0, "DP": 0, "PP": 0, "I": 1, "CO": 1}
INITIAL_PROGRESS = "하나은행 초기 접촉 완료. 담당 과장 미팅 진행. 이상거래탐지 시스템 현황 공유. 예산 규모 약 150억 논의 중."


def main():
    opp_path = DATA / "opportunities.json"
    log_path = DATA / "conversation_log.json"

    # 1) 딜 점수·진행사항 초기화
    opps = json.load(open(opp_path, encoding="utf-8"))
    found = False
    for o in opps:
        if o.get("id") == TARGET_ID:
            o["meddpicc"] = dict(INITIAL_MEDDPICC)
            o["주요_진행사항"] = INITIAL_PROGRESS
            o.pop("최근_업데이트일", None)
            found = True
            break
    if not found:
        print(f"[경고] {TARGET_ID} 딜을 찾지 못했습니다. opportunities.json을 확인하세요.")
        return
    json.dump(opps, open(opp_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 2) 활동 로그 비우기 (이 딜 것만 제거, 다른 딜 로그는 보존)
    try:
        logs = json.load(open(log_path, encoding="utf-8"))
        if not isinstance(logs, list):
            logs = []
    except Exception:
        logs = []
    before = len(logs)
    logs = [a for a in logs if a.get("opp_id") != TARGET_ID]
    json.dump(logs, open(log_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("✅ 리셋 완료")
    print(f"   - {TARGET_ID} meddpicc → {INITIAL_MEDDPICC}")
    print(f"   - 진행사항 → 초기 1줄로 복원")
    print(f"   - 활동 로그 {before - len(logs)}건 제거 (남은 로그 {len(logs)}건)")
    print("   브라우저에서 Ctrl+Shift+R 로 새로고침하세요.")


if __name__ == "__main__":
    main()
