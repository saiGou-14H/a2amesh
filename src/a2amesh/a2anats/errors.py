"""JSON-RPC 错误与错误码。"""

METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
UNAVAILABLE = -32000
TIMEOUT = -32001
CANCELED = -32002
FORBIDDEN = -32003


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message
        super().__init__(message)
