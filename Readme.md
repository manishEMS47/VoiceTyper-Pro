# VoiceTyper Pro

A graphical interface for voice-to-text transcription using Python. This application converts speech to text and automatically types the transcribed text at your cursor position.

It supports **multiple transcription providers** — choose between **60db** and **Deepgram** from the Settings menu. Both backends share the same pipeline, so switching providers is seamless.

Alternative to Mac Whisper, Voice Access, and other voice typing tools.

## Features
- Speech-to-text transcription with **pluggable providers (60db & Deepgram)**
- Switch providers at runtime from the Settings dialog (each provider stores its own API key)
- Automatic text insertion at cursor position
- Keyboard shortcut support (F2)
- Transcription logging
- System tray support
- User-friendly GUI interface

## Requirements
- Python 3.7 or higher
- An API key for at least one provider:
  - **60db** API key (get one at https://60db.ai) — default provider
  - **Deepgram** API key (get one at https://deepgram.com)
- Operating System: Windows, macOS, or Linux

## Setup Instructions

1. Install the required dependencies:

```bash
pip install -r requirements.txt
```

2. Run the application:

```bash
python main.py
```

3. Open **Settings (⚙️)**, choose your provider (**60db** or **Deepgram**), and paste the matching API key. Click **Save**.

4. Enjoy!

## Usage
- Click the "Start Recording" button or press F2 to begin recording
- Click again or press F2 to stop recording
- The transcribed text will appear in the window and be typed at your cursor position
- All transcriptions are logged in `transcribe.log`

## Choosing a provider

| Provider | Backend | Notes |
|----------|---------|-------|
| **60db** (default) | `POST https://api.60db.ai/stt` | REST multipart upload; transcript returned in the `text` field |
| **Deepgram** | Pre-recorded API (deepgram-sdk v2) | Async batch transcription |

Settings are stored in `settings.json`:

```json
{
  "provider": "60db",
  "deepgram_api_key": "your-deepgram-key",
  "60db_api_key": "your-60db-key"
}
```

> Backward compatible: an older `settings.json` containing a single `api_key` is automatically migrated to the Deepgram key on first launch.

## Support
If you find this tool helpful, you can support the development by:
- Buying me a coffee at https://ko-fi.com/perrypixel
- UPI to kevinp@apl
