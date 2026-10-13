from app.api.routes import job_status


class Jobs:
    def get(self, job_id):
        return {
            "job_id": job_id,
            "document_id": "64",
            "document_version": "2",
            "status": "queued",
        }


def test_get_job_exposes_top_level_correlation_fields():
    result = job_status("job-1", jobs=Jobs())
    assert result["document_id"] == "64"
    assert result["document_version"] == "2"
