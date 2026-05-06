# tool-render-video-youtube

Pipeline tự động tạo video YouTube anime tiếng Việt: chủ đề → script → voice → hình ảnh AI → video MP4.

## Chạy nhanh (1 lệnh duy nhất)

```powershell
.\run.ps1 -Topic "chủ đề video của bạn"
```

Script tự động khởi động Ollama + ComfyUI (nếu chưa chạy), rồi render video hoàn chỉnh.

### Ví dụ

```powershell
# Full AI visuals (mất ~2-3 giờ, CPU-only)
.\run.ps1 -Topic "tại sao bạn luôn trì hoãn"

# Nhanh với placeholder visuals (~2-3 phút)
.\run.ps1 -Topic "tại sao bạn luôn trì hoãn" -SkipVisual
```

### Output

Video MP4 xuất ra thư mục `output\`:

```
output\t_i_sao_b_n_lu_n_tr_ho_n_20260505_090447.mp4
```

## Yêu cầu

| Thành phần | Phiên bản | Ghi chú                        |
| ---------- | --------- | ------------------------------ |
| Python     | 3.11+     |                                |
| Ollama     | 0.23.0+   | Model `qwen2.5:7b`             |
| ComfyUI    | latest    | Cài tại `D:\ComfyUI`, CPU mode |
| FFmpeg     | 8.1+      |                                |
| edge-tts   | latest    | Voice `vi-VN-NamMinhNeural`    |

### Models cần thiết

- **Checkpoint**: `D:\ComfyUI\models\checkpoints\anything-v5-PrtRE.safetensors`
- **AnimateDiff**: `D:\ComfyUI\custom_nodes\ComfyUI-AnimateDiff-Evolved\models\mm_sd_v15_v2.ckpt`
