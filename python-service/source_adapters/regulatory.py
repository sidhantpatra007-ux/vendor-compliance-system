class RegulatoryAdapter:
    source_name = "regulatory_enforcement"

    def fetch(self, *args, **kwargs):
        raise RuntimeError("Regulatory-enforcement provider is not configured.")

    def normalize(self, *args, **kwargs):
        return []
