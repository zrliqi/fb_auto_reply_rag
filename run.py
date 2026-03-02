from production_app import create_app

app = create_app()

if __name__ == "__main__":
    cfg = app.extensions["cfg"]
    app.run(host="0.0.0.0", port=cfg["port"])
