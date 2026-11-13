# 작업 인수인계 (Claude Code가 세션 시작 시 읽는 파일)

한국어로 답한다. 사용자는 코딩과 자동매매가 처음이다. 압축 파일을 만들지 말고 이 폴더의 파일을 직접 갱신하며, 작업 단위마다 `git commit` 후 `git push origin main` 한다. API 키는 채팅·코드·커밋에 넣지 않는다. 비용 지출, 계정 생성, 실전 주문처럼 사용자의 결정이 필요한 일에만 멈추고 묻는다. 실제로 실행·검증한 범위를 밝힌다. 전체 요구사항은 `CLAUDE-PROMPT.md`, 실행 방법은 `README.md`에 있다.

## 2026-11-13 세션에서 확인한 것

- `python dashboard.py --no-open`, `demo.py`, `pressure.py` 재현 확인. 결과는 가상 시세이며 실제 데이터가 아니다.
- 데이터 공급 경로 결정: 나스닥 공개 ITCH 5.0 샘플 https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/ 중 하루 전체가 있는 2019년 7개 거래일(01-30, 03-27, 07-30, 08-30, 10-18(S101819), 10-30, 12-30). 파일당 3.5~5.5 GB, 총 약 25 GB. 계정·비용 없음. 2025-12-08~12 연속 5일 파일(S120825~S121225, 8~13 GB씩)도 있어 연속 거래일 기준이 필요하면 다음 후보다.
- LOBSTER 공개 샘플과 Hugging Face 미러는 2012-06-21 하루뿐이라 여러 날짜 비교에 못 쓴다. Alpaca·AlgoSeek은 계정이 필요해 보류.
- 회사 PC에서 12302019 파일을 1.9 GB까지 받다가 중단. 속도 1.7~5.7 MB/s. 순수 Python 추출기의 패킷 읽기 속도는 약 45만 개/초라 하루(약 2.5억 패킷) 추출에 15~20분이 든다.
- 추가한 코드: `PaperBot.trades`에 체결별 손익 기록, `historical.run` 결과에 `filter_avoided_loss`(돌파만 체결한 거래의 손실 합)·`filter_missed_profit`(같은 거래의 이익 합), 대시보드 과거 시험 문구에 표시. 실제 데이터로는 아직 실행하지 못했다.
- 구현과 프롬프트 설명 비교: 15분 범위·거래량 3배·거래대금 100만 달러로 횡보 설정, 10분 안에 두 분봉 연속 상단 위 마감이면 신호, 다음 분봉 종가가 상단 위일 때 그 분봉의 매도호가에 체결, 상단 아래 마감 또는 30분 보유면 다음 분봉 매수호가에 청산. 여러 날짜 모드의 기준은 이전 거래일 같은 15분 구간의 중앙값만 쓰므로 미래 정보는 쓰지 않는다. 체결 방향은 ITCH 표시 주문 체결의 반대편(LOBSTER 규약과 동일)이며 비표시 체결은 방향 미상으로 거래량에만 들어간다.
- 같은 날 저녁 집 PC: Python 3.12.10을 winget으로 사용자 범위 설치(`C:\Users\artso\AppData\Local\Programs\Python\Python312`, 기존 셸은 PATH 미반영). 다운로드 약 12 MB/s. 다운로드·추출 스크립트를 백그라운드로 시작했다.
- 시장 거부권을 과거 시험에 연결: `historical.run`이 `sessions` 옆 `index/`(또는 `--index`)의 SPY·QQQ로 분마다 VWAP 아래 여부와 거래 대상 중 당일 첫 체결가 아래 비율을 계산해 `Market`에 넣고, `breakout_market_veto`·`pressure_market_veto` 전략을 추가한다. 결과 키 `market_filter_dates`, `weak_market_minutes`, `market_avoided_loss`, `market_missed_profit`. 합성 3일 데이터로만 검증했다.
- ORB 비교 추가: `orb.py`의 `OpeningRangeScanner`(개장 5분 범위, 이전 거래일 대비 상대 거래량 1배 이상·양봉 상위 3종목, 범위 고가 위 마감 시 신호, 청산은 기존 PaperBot 규칙). 과거 시험에 `orb`, `orb_market_veto` 전략과 `orb_selections` 결과 추가. 합성 데이터로만 검증.
- ITCH 서버의 .md5sum은 목록에는 있지만 404라서 `download-itch.sh`는 크기 + `gzip -t`로 검증한다.
- 이 PC의 git 사용자 정보는 저장소 로컬 설정으로 넣었고 GitHub 로그인은 완료했다.
- 다운로드가 커지는 문제의 원인은 TaskStop으로 멈춘 줄 알았던 이전 스크립트가 살아 있어 같은 파일에 동시에 쓴 것. `download-itch.sh`에 `download.lock` 잠금을 넣었다. 7일 모두 크기+gzip 검사 통과 후 추출 완료(`D:\us-stock-data\sessions`, `index`).
- 2026-11-14 7일 실제 비교 실행(약 6분), 결과는 README "2019년 7일 실제 결과" 표. 매수 압력 통과 0. 1회 한도 $1,000에서는 AMZN·GOOG를 1주도 못 사서, 사용자 결정으로 `bot.py`의 `TRADE_BUDGET`을 $2,500으로 올렸다. $2,500 기준 돌파 4건 -$17.94, ORB 5건 -$22.56, ORB+시장 거부권 4건 -$11.46. 표본이 적어 우열 판단 불가.
- 아직 반영 안 된 것: 개장·마감 경매 체결(Q 메시지) 제외, 사후 체결 취소 미반영, `Bar.halted`는 데이터에서 채우지 않음(정지 중에는 호가가 0이라 매수 불가로만 처리), 소셜 거부권은 과거 시험에 미적용, UTC 오프셋은 실행당 하나만 사용(표시는 거래소 현지시간이라 영향 없음).

## 다음 작업 순서

1~3번은 2026-11-14에 완료(이 PC에는 데이터가 있음). 새 PC에서 재현할 때만 1번부터 한다.

1. Git Bash 두 창에서 `sh scripts/download-itch.sh`와 `sh scripts/extract-itch.sh`를 실행한다(다른 드라이브면 두 스크립트의 `/d/us-stock-data` 경로를 바꾼다). 다운로드는 이어받기와 md5 검증을 한다. SPY·QQQ는 `index/`로 옮겨져 거래 대상에서 빠진다.
2. `python dashboard.py --historical "D:\us-stock-data\sessions" --utc-offset=-5`로 비교를 돌리고 `historical-dashboard.html`을 커밋한다. 첫 날짜는 기준만 쌓으므로 6일이 평가된다.
3. 거래 수, 순손익, 이익·손실 합, 최대 낙폭, 필터(매수 압력·시장 거부권)로 피한 손실·놓친 수익을 README에 기록한다. 거래가 적으면 그대로 표시하고 기준을 표본에 맞춰 낮추지 않는다.
4. 그다음: 실시간 모의매매, X·Reddit 실제 저장과 DeepSeek 분석(후보 종목 한정).
