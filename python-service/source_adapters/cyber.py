class CyberAdapter:
    source_name = "cyber"

    def fetch(self, *args, **kwargs):
        raise RuntimeError("Cyber-risk provider is not configured.")

    def normalize(self, *args, **kwargs):
        return []
