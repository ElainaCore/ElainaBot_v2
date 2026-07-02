from .PromptException import PromptException as pe


class PromptTpl:
    UploadMediaFail = pe('upload_media_bytes{retry_msg}.fail:{e}', 5001001)
