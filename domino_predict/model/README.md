# model/

Local 모드 추론에 쓰는 학습된 모델입니다. **Install Local Environment**가 이 폴더의 파일을
WSL의 `~/domino-ahmedml/model/`로 복사합니다 (따로 내려받지 않음).

- `DoMINO.0.220.mdlus` — AhmedML 400개로 학습한 DoMINO 모델의 best checkpoint (epoch 220, best val loss 0.00171)
- `scaling_factors.pkl` — 학습 때 계산한 정규화 통계 (추론 시 반드시 이 파일을 그대로 재사용해야 함)

학습 과정과 설정 파일은 [domino-ahmedml-pipeline](https://github.com/EOKYEONGBIN/domino-ahmedml-pipeline)에 있습니다.

## 라이선스 — 코드와 다릅니다

이 모델은 코드가 아니라 **AhmedML 데이터셋으로 학습한 결과물**입니다. AhmedML은
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 라이선스라, 이 체크포인트도
저장소 전체의 Apache 2.0이 아니라 **CC BY-SA 4.0**을 따릅니다:

- **저작자 표시**: AhmedML 데이터셋 — N. Ashton, D. C. Maddix, S. Gundry, P. M. Shabestari,
  "AhmedML: High-Fidelity Computational Fluid Dynamics Dataset for Incompressible,
  Low-Speed Bluff Body Aerodynamics," arXiv:2407.20801, 2024.
  ([caemldatasets.org/ahmedml](https://caemldatasets.org/ahmedml/))
- **동일 라이선스 유지(ShareAlike)**: 이 체크포인트를 가져다 쓰거나 재배포할 때도 CC BY-SA 4.0을
  유지해야 합니다.
