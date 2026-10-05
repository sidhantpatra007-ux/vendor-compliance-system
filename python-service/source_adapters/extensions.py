class NotConfiguredSourceAdapter:
    source_name = "unknown"

    def fetch(self, *args, **kwargs):
        raise RuntimeError(f"{self.source_name} is not configured. Review provider reliability, licensing, retention, and commercial-use terms first.")

    def normalize(self, *args, **kwargs):
        return []


class PepAdapter(NotConfiguredSourceAdapter):
    source_name = "pep"


class DebarmentAdapter(NotConfiguredSourceAdapter):
    source_name = "debarment"


class RegulatoryEnforcementAdapter(NotConfiguredSourceAdapter):
    source_name = "regulatory_enforcement"


class CyberAdapter(NotConfiguredSourceAdapter):
    source_name = "cyber"
