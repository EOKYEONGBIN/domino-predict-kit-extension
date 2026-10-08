# DoMINO Prediction — Kit-CAE extension

Kit-CAE(Omniverse) 안에서 STL 형상을 고르고 버튼 하나로 **학습된 DoMINO 모델에 추론을 요청**한 뒤,
돌아온 결과를 **Kit-CAE 시각화(Faces / Streamlines)로 자동 세팅**해주는 익스텐션입니다.

추론은 두 가지 방식 중 하나로 돌립니다.

```
                              ┌─ Remote ─▶ 같은 네트워크의 추론 서버 (SSH/SCP)
Kit-CAE (Windows)             │            └ ~/domino-ahmedml/scripts/run_prediction.sh
 DoMINO Prediction ── STL ────┤
   결과 자동 시각화 ◀─ VTP/VTI─┤
                              └─ Local ──▶ 이 PC의 WSL2 (wsl.exe), 이 PC의 GPU로 추론
                                           └ ~/domino-ahmedml/scripts/run_prediction.sh
```

## 화면 구성

```
[Server Settings]  - Connect          ← 버튼을 누르면 아래 설정이 펼쳐짐. 상태는 초록/빨강
   [x] Local (WSL2)   [ ] Remote      ← 둘 중 하나만 선택됨
   [Install Local Environment]        ← Local일 때만 보임. WSL 안에 추론 환경 자동 설치
   Server IP: [192.168.x.x]           ← Remote일 때만 보임
   [Connect]                          ← 지금 모드로 연결 확인
[x] Legend UI                         ← 뷰포트 범례 켜기/끄기 (기본 켜짐)
Input STL: [..............] [Browse...]
Visualize: [x] Faces  [x] Streamlines
[Request Prediction]
[Save Boundary (.vtp)] [Save Volume (.vti)]   ← 최근 결과를 원하는 위치에 저장
Prediction imported (Local). Total 1m 52s (inference 1m 50s).   ← 완료 시 소요시간
```

- **Server Settings**: 누르면 설정이 펼쳐지고 다시 누르면 접힙니다. 옆에 연결 상태가 `Connect`(초록) / `Disconnect`(빨강)로 표시됩니다.
- **Local / Remote**: 하나를 켜면 다른 하나는 자동으로 꺼집니다. 모드를 바꾸면 바로 연결을 다시 확인합니다.
  - **Local**: 이 PC의 WSL2에서 추론합니다. Connect는 WSL → GPU → 추론 환경 순서로 확인하고, 실패하면 무엇이 없는지 상태 줄에 알려줍니다.
  - **Install Local Environment**: WSL 안에 추론 환경(가상환경, PyTorch, cuML, physicsnemo 패치, 학습된 모델)을 설치합니다. 진행 단계가 상태 줄에 표시되고, 끝나면 자동으로 Connect를 확인합니다.
  - **Remote**: 입력한 서버 IP로 SSH 접속해 추론합니다.
- **설정 저장**: 모드와 서버 IP는 `~/.domino_predict_settings.json`에 저장돼서, Kit-CAE를 다시 켜도 유지됩니다. 켜질 때 저장된 설정으로 자동 연결 확인을 합니다.
- **Streamlines 자동 설정**: UMean 속도로 유선을 만들고(진행 방향 forward, 굵기 0.02), 색은 속도 크기(`vector_magnitude`)로 **0 ~ 1.6** 고정 범위입니다(Rescale Mode `disable`, ScalarColor·AnimatedStreaks 두 셰이더 모두). 범위는 AhmedML CFD 체적 450개(전처리된 train/val)에서 케이스별 99.9% 값이 95%의 케이스에서 1.6 이하인 것을 기준으로 정했습니다. CFD 유선과 비교할 때는 CFD 쪽 Scalar Domain도 (0, 1.6)으로 맞추면 됩니다. 시작점 구는 형상 앞쪽(형상 길이의 0.8배 앞), 반지름은 폭·높이 중 작은 값의 0.4배입니다.
- **결과 저장**: Save Boundary (.vtp) / Save Volume (.vti) 버튼으로 가장 최근 요청의 결과를 원하는 폴더에 따로 저장합니다. 저장하면 장면의 데이터셋(`DominoPrediction_N`, `DominoVolumePrediction_N`)이 저장한 파일을 보도록 바뀌어서, 장면을 Save As 한 뒤 임시 폴더가 지워져도 다시 열면 결과가 그대로 나옵니다. 받아 온 결과는 기본으로 Windows 임시 폴더(`%TEMP%\domino_predict_<요청ID>_...`)에 있습니다.
- **소요시간**: 요청이 끝나면 전체 시간과 그중 추론에 걸린 시간을 상태 줄에 남깁니다.
- **시각화**: Faces(표면 압력 / 벽전단응력), Streamlines(체적 속도장 유선)를 골라서 요청할 수 있고, 받은 결과로 Kit-CAE 연산자를 자동으로 만듭니다.
- **고정 색상 범위 + 범례**: 예측 Faces의 pMean 색상 범위를 항상 **-1.00 ~ 0.52**로 고정합니다(자동 재조정 끔). 그래서 어떤 STL을 넣어도 같은 압력은 같은 색입니다. 범위는 AhmedML CFD 500개 전체에서 정했습니다. 뷰포트 오른쪽 아래 범례에 DoMINO가 예측하는 필드의 범위를 단위와 함께 표시합니다 (Faces: pMean (m^2/s^2) -1.00 ~ 0.52, Cp (-) -2.00 ~ 1.04, 벽 전단응력 크기 (m^2/s^2) 0 ~ 0.007 / Streamlines: UMean 크기 (m/s) 0 ~ 1.6, pMean (m^2/s^2) -1.00 ~ 0.52, nutMean (m^2/s) 0 ~ 1.5e-4). 압력·전단응력은 밀도로 나눈 값입니다(OpenFOAM 비압축성). 다른 필드로 바꿔 볼 때는 Rescale Mode `disable` 상태에서 Shader의 Scalar Domain을 범례 값으로 맞추면 됩니다(벡터는 Field Selection Mode `vector_magnitude`). 범례는 익스텐션이 켜지면 바로 표시되고, 창의 **Legend UI** 토글로 켜고 끌 수 있습니다(기본 켜짐). 범위는 `extension.py`의 `PMEAN_RANGE_*`, `UMAG_RANGE_*`, `NUT_RANGE_*`, `LEGEND_FIELDS`에서 바꿉니다.

## 구성

```
domino_predict/                      ← 익스텐션 폴더
├── config/extension.toml
├── domino_predict/
│   ├── extension.py                 ← 시작/종료, 요청 흐름, 설치, 소요시간, 결과를 Kit-CAE 시각화로 세팅
│   ├── predict_window.py            ← UI
│   ├── legend.py                    ← 뷰포트 오른쪽 아래 색상 범례 (컬러바 1개 + Faces 3줄 / Streamlines 3줄)
│   ├── remote_predict.py            ← Local(WSL2) / Remote(SSH) 요청, 연결 확인, 설치 실행, 설정 저장
│   └── cae_viz_helpers.py           ← Kit-CAE 연산자 완료 대기 등 보조 함수
└── wsl_setup/
    └── setup_local_inference.sh     ← Local 모드용 WSL2 환경 설치 (Install 버튼이 실행)
```

## 익스텐션 설치

1. 이 저장소를 받습니다.
2. Kit-CAE의 **Extensions → 설정(⚙) → Extension Search Paths**에 **이 저장소의 루트 폴더**를 추가합니다.
   - `domino_predict` 폴더 자체가 아니라 **그 상위 폴더**를 넣어야 합니다. 익스텐션 폴더를 직접 넣으면 목록에는 보이지만 실행 시 `ModuleNotFoundError: No module named 'domino_predict'`가 납니다.
3. Extensions 목록에서 **DoMINO Prediction**을 켭니다.

## Local 모드 준비 (이 PC의 WSL2에서 추론)

필요한 것: NVIDIA GPU(VRAM 8GB 이상 권장, 추론 1회 약 5GB 사용), Windows용 NVIDIA 드라이버.

**1. WSL2 설치 (직접, 처음 한 번)** — 관리자 권한과 재부팅이 필요해서 익스텐션이 대신하지 않습니다.
```powershell
# 관리자 PowerShell
wsl --install -d Ubuntu-24.04
```
재부팅 후 시작 메뉴에서 **Ubuntu**를 한 번 실행해 사용자 계정을 만듭니다.

**2. 추론 환경 설치 (버튼 하나)** — 익스텐션에서 **Server Settings → Local (WSL2) → Install Local Environment**.
약 7GB를 받으므로 처음에는 10~20분 정도 걸립니다. sudo는 필요 없습니다. 끝나면 자동으로 Connect가 확인되고 `Connect`(초록)가 됩니다.

> WSL 터미널에서 직접 실행해도 됩니다: `bash /mnt/c/<이 저장소 경로>/domino_predict/wsl_setup/setup_local_inference.sh`

설치 스크립트가 하는 일:
- 추론 전용 가상환경 `~/venvs/domino_infer` 생성 (torch 2.14.0+cu130, physicsnemo 2.2.2, cuML 26.8)
- physicsnemo 2.2.2 버그 패치: `VTKFileReader`에 `read_file_attributes`가 없어서 STL을 읽지 못하는 문제
- 추론 코드, 설정, 학습된 모델을 [domino-cfd-pipeline-guide](https://github.com/EOKYEONGBIN/domino-cfd-pipeline-guide), [domino-ahmedml-pipeline](https://github.com/EOKYEONGBIN/domino-ahmedml-pipeline)에서 받아 `~/domino-ahmedml`에 배치

> **cuML이 꼭 필요합니다.** 없으면 physicsnemo가 최근접 이웃 검색을 전체 거리 행렬을 만드는 PyTorch 구현으로 대체해서, 12GB급 GPU에서 `CUDA out of memory`가 납니다. 설치 스크립트가 함께 설치합니다.

## Remote 모드 준비 (추론 서버)

- SSH 서버 + 이 PC의 공개키 등록 (비밀번호 없이 접속). 익스텐션은 `~/.ssh/id_ed25519` 키를 사용합니다.
- 서버에 `~/domino-ahmedml/scripts/run_prediction.sh`, 학습된 모델(`~/domino-ahmedml/model/`), 설정 파일이 있어야 합니다. 위 두 저장소를 참고하세요. 서버에도 위의 physicsnemo 패치와 cuML이 필요합니다.
- `remote_predict.py`의 `SSH_USER`를 서버 계정명으로 바꾸고, 서버 IP는 UI에서 입력합니다.

## 참고 측정 (같은 STL, Faces + Streamlines)

| 방식 | 장비 | 요청 1회 |
|---|---|---|
| Remote | RTX A6000 (전력 160W 제한), Ryzen 9 7950X | 약 66초 |
| Local | RTX 5070 Ti Laptop (WSL2), Ryzen AI 7 350 | 약 110초 |

두 방식의 결과는 사실상 같습니다 (서로 비교한 표면 압력 R² 0.9999).

## 라이선스

Apache License 2.0. 단, 이 익스텐션으로 불러오는 DoMINO 모델은 학습 데이터(AhmedML)를 따라 CC BY-SA 4.0입니다.
