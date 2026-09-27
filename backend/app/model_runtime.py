from dataclasses import dataclass


@dataclass
class ModelRuntime:
    active: bool = False
    message: str = "尚未启动"


runtime = ModelRuntime()
