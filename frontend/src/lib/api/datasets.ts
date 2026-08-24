import { apiClient } from "@/lib/api/client";
import { API_URL } from "@/lib/env";
import { getAuthToken } from "@/stores/auth-store";
import { ApiError } from "@/types/api";
import type { DatasetProfile, DatasetV1ListResponse, DatasetV1Response } from "@/types/dataset";
import type { DataQualityReport } from "@/types/deep-analysis";

export type UploadPhase = "uploading" | "processing";

/**
 * Uploads via XMLHttpRequest (not `fetch`) specifically to get real
 * `upload.onprogress` events -- `fetch` has no reliable, broadly-supported
 * way to report upload (request body) progress. Once the browser finishes
 * sending the file, the backend is still parsing/validating/profiling/
 * registering it in DuckDB before it responds (see
 * services/dataset_service.py::upload_dataset) -- that real wait is
 * reported as the "processing" phase, not a fabricated timer.
 */
function uploadWithProgress(file: File, onPhaseChange: (phase: UploadPhase, uploadPercent: number) => void): Promise<DatasetV1Response> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/api/v1/datasets`);
    const token = getAuthToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const percent = Math.round((event.loaded / event.total) * 100);
        onPhaseChange(percent < 100 ? "uploading" : "processing", percent);
      }
    };

    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // handled by the status check below
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as DatasetV1Response);
      } else {
        const detail = body && typeof body === "object" && "detail" in body ? String((body as { detail: unknown }).detail) : "Failed to upload the dataset.";
        reject(new ApiError(xhr.status, detail));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error while uploading the dataset."));

    xhr.send(formData);
  });
}

// backend/app/api/routes/datasets_v1.py
export const datasetsApi = {
  upload: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post<DatasetV1Response>("/api/v1/datasets", formData);
  },
  uploadWithProgress,
  list: () => apiClient.get<DatasetV1ListResponse>("/api/v1/datasets"),
  get: (datasetId: string) => apiClient.get<DatasetV1Response>(`/api/v1/datasets/${datasetId}`),
  delete: (datasetId: string) => apiClient.delete<void>(`/api/v1/datasets/${datasetId}`),
  profile: (datasetId: string) => apiClient.get<DatasetProfile>(`/api/v1/datasets/${datasetId}/profile`),
  quality: (datasetId: string) => apiClient.get<DataQualityReport>(`/api/v1/datasets/${datasetId}/quality`),
};
