class PepAdapter:
    source_name = "pep"

    def fetch(self, *args, **kwargs):
        raise RuntimeError("PEP screening provider is not configured.")

    def normalize(self, *args, **kwargs):
        return []
