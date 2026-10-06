# DoMINO Prediction — Kit-CAE extension

Kit-CAE(Omniverse) 안에서 STL 형상을 고르고 버튼 하나로 **학습된 DoMINO 모델에 추론을 요청**한 뒤,
돌아온 결과를 **Kit-CAE 시각화(Faces / Streamlines)로 자동 세팅**해주는 익스텐션입니다.

```
Kit-CAE (Windows)                         추론 서버 (Linux, NVIDIA GPU)
┌──────────────────────┐   SSH/SCP   ┌──────────────────────────────────┐
│ DoMINO Prediction UI │ ──STL 전송──▶│ scripts/run_prediction.sh        │
│  - 서버 연결 확인    │             │  └ predict_on_stl.py (DoMINO 추론)│
│  - Faces/Streamlines │ ◀─VTP/VTI───│                                  │
│  - 결과 자동 시각화  │             └──────────────────────────────────┘
└──────────────────────┘
```

## 기능

- **서버 설정**: 추론 서버 IP 입력 후 `Connect`로 접속 확인. 상태는 `Connect`(초록) / `Disconnect`(빨강)로 표시. 입력한 IP는 다음 실행 때도 기억함 (`~/.domino_predict_settings.json`)
- **입력**: STL 파일 선택
- **시각화 선택**:
  - Faces — 표면 압력 / 벽전단응력 (`prediction_0.vtp`)
  - Streamlines — 체적 속도장 유선 (`prediction_volume_grid_0.vti`)
- **자동 세팅**: 결과를 스테이지에 불러와 Faces / Streamlines / BoundingBox 연산자를 만들고 필드까지 지정

## 구성

```
domino_predict/                  ← 익스텐션 폴더
├── config/extension.toml        ← 익스텐션 정보, 의존 Kit-CAE 모듈
└── domino_predict/
    ├── extension.py             ← 시작/종료, 요청 흐름, 결과를 Kit-CAE 시각화로 세팅
    ├── predict_window.py        ← UI
    ├── remote_predict.py        ← SSH/SCP로 서버에 추론 요청, 설정 저장
    └── cae_viz_helpers.py       ← Kit-CAE 연산자 완료 대기 등 보조 함수
```

## 설치

1. 이 저장소를 받습니다.
2. Kit-CAE의 **Extensions → 설정(⚙) → Extension Search Paths**에 **이 저장소의 루트 폴더**를 추가합니다.
   - 주의: `domino_predict` 폴더 자체가 아니라 **그 상위 폴더**를 추가해야 합니다. 익스텐션 폴더를 직접 넣으면 목록에는 보이지만 실행 시 `ModuleNotFoundError: No module named 'domino_predict'`가 납니다.
3. Extensions 목록에서 **DoMINO Prediction**을 켭니다.

## 추론 서버 준비

서버에는 아래가 필요합니다.

- SSH 서버 + 이 PC의 공개키 등록 (비밀번호 없이 접속). 익스텐션은 `~/.ssh/id_ed25519` 키를 사용합니다.
- `~/domino-ahmedml/scripts/run_prediction.sh`, 학습된 모델(`~/domino-ahmedml/model/`), 설정 파일
  - 추론 코드: [domino-cfd-pipeline-guide](https://github.com/EOKYEONGBIN/domino-cfd-pipeline-guide)
  - 설정 / 모델: [domino-ahmedml-pipeline](https://github.com/EOKYEONGBIN/domino-ahmedml-pipeline)

`remote_predict.py`의 `SSH_USER`를 서버 계정명으로 바꿔서 쓰세요. 서버 IP는 UI에서 입력합니다.

## 라이선스

Apache License 2.0. 단, 이 익스텐션으로 불러오는 DoMINO 모델은 학습 데이터(AhmedML)를 따라 CC BY-SA 4.0입니다.
