# PyAudioCore Service

A high-performance Python audio streaming and metadata service.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/yohanrock/py-audio-core)

## Features
- Fast audio stream indexing and metadata management
- High quality stream processing (up to 320kbps)
- Dual-provider search and caching engine
- Built-in web server with health-check endpoints

## Deployment

### Deploy to Render
Click the **Deploy to Render** button above, or deploy manually:
- **Runtime:** Python 3
- **Build Command:** `pip install -U pip && pip install -r requirements.txt`
- **Start Command:** `python -m jiosaavn`
- **Python Version:** `3.11.9`

## Configuration
- `API_ID`: Telegram API ID
- `API_HASH`: Telegram API Hash
- `BOT_TOKEN`: Telegram Bot Token
- `DATABASE_URL`: MongoDB storage connection string
- `HOST`: Server host (`0.0.0.0`)
- `PORT`: Server port (`8080`)
