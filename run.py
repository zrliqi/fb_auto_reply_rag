from bot_app import create_app

app = create_app()

if __name__ == "__main__":
    settings = app.extensions["settings"]
    app.run(host="0.0.0.0", port=settings.port)

