from .prompt_exception import PromptException


class PromptTpl:
    UploadMediaFail = PromptException('upload_media_bytes{retry_msg}.fail:{e}', 5001001)
