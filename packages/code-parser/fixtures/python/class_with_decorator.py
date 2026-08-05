from dataclasses import dataclass


@dataclass
class Greeter:
    prefix: str

    def greet(self, name: str) -> str:
        return self.prefix + name
