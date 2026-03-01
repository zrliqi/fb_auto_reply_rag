# RAG Chat Bot

A Facebook Page bot with RAG (Retrieval-Augmented Generation) capabilities. Uses local LLMs via Ollama.

## Features

- **RAG-powered Q&A** - Answers questions from uploaded documents
- **Multi-format support** - TXT, PDF, DOCX, CSV
- **Multi-user memory** - Each Facebook user gets persistent conversation history
- **Folder upload** - Upload entire folders at once
- **Query refinement** - Understands follow-up questions ("he" → previous subject)
- **Facebook Messenger integration** - Webhook skeleton ready

## Requirements

- Python 3.12+
- Ollama running locally

## Quick Start

### 1. Install Ollama

```bash
# macOS/Linux
curl -fsSL https://ollama.com/install.sh | sh

# Start Ollama
ollama serve

# Pull required models
ollama pull qwen2.5:3b
ollama pull bge-m3:latest
```

### 2. Setup Python Environment

```bash
# Create virtual environment
python -m venv .venv

# Activate
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
# Copy environment template
cp .env.example .env

# Edit .env (optional - for Facebook integration)
```

### 4. Run the Application

```bash
python app.py
```

Open http://localhost:5000 in your browser.

---

## Usage

### Web Interface

1. **Chat** - Ask questions about your documents
2. **Documents** - Upload files, click "Update KB" after uploading

### Facebook Integration (Optional)

1. Create app at [developers.facebook.com](https://developers.facebook.com)
2. Add Messenger product
3. Get Page Access Token
4. Edit `.env` with your credentials
5. Implement `send_fb_message()` in `fb_bot.py`
6. Configure webhook URL (use ngrok for local testing)

---

## Project Structure

```
├── app.py          # Flask web routes
├── rag.py          # RAG engine
├── fb_bot.py       # Facebook webhook
├── requirements.txt # Dependencies
├── .env.example    # Environment template
├── .gitignore      # Git ignore rules
└── uploads/        # Uploaded documents
```

---

## Commands

```bash
# Run the app
python app.py

# Update knowledge base (after uploading files)
# Click "Update KB" button in UI or POST to /api/reload
curl -X POST http://localhost:5000/api/reload
```

---

## Troubleshooting

### No documents loaded
- Upload files to `uploads/` folder
- Click "Update KB" button

### Model not found
```bash
ollama list
ollama pull qwen2.5:3b
ollama pull bge-m3:latest
```

### Facebook webhook not working
- Use ngrok: `ngrok http 5000`
- Configure webhook URL in Facebook Developer Console
- Verify token must match `FB_VERIFY_TOKEN` in `.env`

---

## License

MIT
