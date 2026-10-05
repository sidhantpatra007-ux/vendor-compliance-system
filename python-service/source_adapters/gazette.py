class GazetteAdapter:
    source_name = "gazette"

    def fetch(self, *args, **kwargs):
        raise RuntimeError("Gazette is not configured. Verify provider terms before enabling it.")

    def normalize(self, *args, **kwargs):
        return []
