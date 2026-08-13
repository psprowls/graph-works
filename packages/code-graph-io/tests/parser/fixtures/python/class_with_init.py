class Greeter:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def greet(self, name: str) -> str:
        return self.prefix + name
