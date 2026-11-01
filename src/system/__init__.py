from system.collect import collect, save


def main() -> None:
    path = save(collect())
    print(f"Saved system info to {path}")
