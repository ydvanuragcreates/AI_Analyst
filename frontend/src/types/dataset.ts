// Mirrors backend/app/schemas/dataset.py and dataset_v1.py exactly.

export interface DatasetColumn {
  name: string;
  data_type: "string" | "integer" | "float" | "boolean" | "datetime" | "object";
  nullable: boolean;
}

export type DatasetPreview = Record<string, unknown>[];

export interface DatasetMetadata {
  dataset_id: string;
  filename: string;
  file_type: string;
  file_size: number;
  row_count: number;
  column_count: number;
  columns: DatasetColumn[];
  missing_values: Record<string, number>;
  preview: DatasetPreview;
  uploaded_at: string;
}

export interface DatasetV1Response {
  dataset_id: string;
  version: number;
  metadata: DatasetMetadata;
}

export interface DatasetV1ListResponse {
  datasets: DatasetV1Response[];
  count: number;
}

// Mirrors backend/app/schemas/deep_analysis.py::DatasetProfile and friends
// (Phase 10 autonomous profiling) -- GET /api/v1/datasets/{id}/profile.

export type ColumnKind = "numeric" | "categorical" | "date" | "boolean" | "other";

export interface TopValue {
  value: string | number | boolean | null;
  count: number;
}

export interface ColumnProfile {
  name: string;
  dtype: string;
  kind: ColumnKind;
  missing_count: number;
  missing_percentage: number;
  distinct_count: number;
  is_likely_identifier: boolean;
  is_high_cardinality: boolean;
  min_value: number | null;
  max_value: number | null;
  mean: number | null;
  median: number | null;
  stddev: number | null;
  q1: number | null;
  q3: number | null;
  min_date: string | null;
  max_date: string | null;
  top_values: TopValue[];
}

export interface DatasetProfile {
  dataset_id: string;
  row_count: number;
  column_count: number;
  columns: ColumnProfile[];
  numeric_columns: string[];
  categorical_columns: string[];
  date_columns: string[];
  missing_columns: string[];
  high_cardinality_columns: string[];
  identifier_columns: string[];
}
