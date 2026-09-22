/** Builders for the Models tab specs (imported by specs only - never by app code). */
import {
  DriftOverview,
  DriftReport,
  ModelSummary,
  ModelVersion,
  ModelsOverview,
  QueueOverview,
  RetrainingJob,
  RetrainingJobDetail,
} from './model-ops.models';

export function version(overrides: Partial<ModelVersion> = {}): ModelVersion {
  return {
    id: 'v-1',
    model_name: 'pcb_body_defect',
    version: 'JcProg/body@v1',
    repo_id: 'JcProg/body',
    revision: 'v1',
    status: 'live',
    source_job_id: null,
    first_seen_at: '2026-09-20T10:00:00Z',
    activated_at: '2026-09-20T10:00:00Z',
    activated_by: null,
    ...overrides,
  };
}

export function model(overrides: Partial<ModelSummary> = {}): ModelSummary {
  const live = version();
  return {
    name: 'pcb_body_defect',
    live,
    previous: null,
    versions: [live],
    labels: ['Golden', 'MissingPart'],
    loaded_at: '2026-09-20T10:00:00Z',
    open_drift_reports: 0,
    open_tickets: 0,
    active_jobs: 0,
    ...overrides,
  };
}

export function overview(overrides: Partial<ModelsOverview> = {}): ModelsOverview {
  return {
    inference: { configured: true, reachable: true, error: null },
    models: [model()],
    ...overrides,
  };
}

export function report(overrides: Partial<DriftReport> = {}): DriftReport {
  return {
    id: 'r-1',
    model_name: 'pcb_body_defect',
    model_version: 'JcProg/body@v1',
    description: 'False Tombstone calls since Monday',
    case_numbers: ['CASE-000007'],
    stats: {
      signals: ['override rate rose from 0% to 80%'],
      recent: {
        cases: 12,
        needs_review: 9,
        approved: 2,
        overridden: 8,
        review_rate: 0.75,
        override_rate: 0.8,
        low_confidence_rate: 0.1,
        mean_confidence: 0.82,
      },
    },
    status: 'open',
    reported_by: 'jane-qa',
    created_at: '2026-09-20T11:00:00Z',
    resolved_at: null,
    ...overrides,
  };
}

export function drift(overrides: Partial<DriftOverview> = {}): DriftOverview {
  return {
    window_days: 30,
    open_by_model: { pcb_body_defect: 1 },
    reported_in_window_by_model: { pcb_body_defect: 1 },
    reports: [report()],
    ...overrides,
  };
}

export function job(overrides: Partial<RetrainingJob> = {}): RetrainingJob {
  return {
    id: 'j-1',
    model_name: 'pcb_body_defect',
    base_version: 'JcProg/body@v1',
    status: 'pending_approval',
    rationale: 'Retrain on flagged false positives.',
    sample_count: 2,
    drift_report_ids: [],
    created_by: 'jane-qa',
    approved_by: null,
    approved_at: null,
    external_job_id: null,
    progress: 0,
    error: null,
    artifact_repo_id: null,
    artifact_revision: null,
    simulated: null,
    created_at: '2026-09-20T12:00:00Z',
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

export function jobDetail(overrides: Partial<RetrainingJobDetail> = {}): RetrainingJobDetail {
  return {
    ...job(),
    samples: [
      {
        case_id: 'c-1',
        case_number: 'CASE-000007',
        sample_ref: null,
        ticket_id: 't-1',
        observed_label: 'MissingPart',
        expected_label: 'Golden',
      },
      {
        case_id: 'c-2',
        case_number: null,
        sample_ref: null,
        ticket_id: 't-2',
        observed_label: null,
        expected_label: null,
      },
      {
        case_id: null,
        case_number: null,
        sample_ref: 'S1',
        ticket_id: 't-3',
        observed_label: 'MissingPart',
        expected_label: null,
      },
    ],
    ...overrides,
  };
}

export function queue(overrides: Partial<QueueOverview> = {}): QueueOverview {
  return {
    inference: { configured: true, reachable: true, error: null },
    tickets: { open: 1, acknowledged: 2, resolved: 3 },
    jobs: [job()],
    ...overrides,
  };
}
