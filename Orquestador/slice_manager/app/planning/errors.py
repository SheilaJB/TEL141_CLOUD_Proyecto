class InvalidSpecError(ValueError):
    def __init__(self, message: str, *, path: str = "$") -> None:
        self.path = path
        super().__init__(f"{path}: {message}")
