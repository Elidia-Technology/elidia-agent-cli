# Creative

## Image Generation

```
> /create image A cyberpunk cityscape at night
> /create image Portrait of a cat --model flux-1.1-pro
> /create image Abstract art --model dall-e-3
```

### Image Models

| Model | Vendor | Max Size |
|-------|--------|----------|
| `flux-1.1-pro` | BFL | 2048px |
| `dall-e-3` | OpenAI | 1024px |
| `stable-diffusion-xl` | Stability | 1024px |
| `ideogram` | Ideogram | 2048px |

Images are saved to `~/.elidia/media/images/` and displayed in the terminal (when supported).

### Terminal Display

Elidia auto-detects terminal image protocol support:

- **iTerm2** — inline images via escape sequences
- **Kitty** — Kitty graphics protocol
- **Sixel** — Sixel graphics
- **None** — file path only (saves to disk)

## Video Generation

```
> /create video A timelapse of a sunset over mountains
> /create video Ocean waves crashing --model minimax-video
```

Videos are saved to `~/.elidia/media/videos/`.

## Speech (TTS)

```
> /create speech "Hello, welcome to Elidia" --voice alloy
> /create speech "Important announcement" --voice nova
```

### Voices

Available voices for `tts-1`: `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`

## Music Generation

```
> /create music An upbeat jazz piano track
> /create music Ambient electronic meditation music
```

## List All Models

```
> /create models
```
