declare const __TENDERCITE_CONFIGURED__: boolean;
export const tenderciteConfigured = () =>
  typeof __TENDERCITE_CONFIGURED__ !== 'undefined' && __TENDERCITE_CONFIGURED__;

export interface TenderDocument {
  id: string;
  filename: string;
  sha256: string;
  page_count: number;
  chunk_count: number;
  created_at: string;
}
export interface TenderFinding {
  id: string;
  analysis_run_id: string;
  statement: string;
  category: string;
  requirement_type: string;
  effective_value: { statement: string; category: string; requirement_type: string };
  review_status: string;
  grounding_status: string;
  evidence: { document_id: string; page_number: number; quote: string; grounding_status: string }[];
}
export interface TenderAnalysis {
  id: string;
  provider: string;
  model: string;
  created_at: string;
}
// Separate client: never accept a browser-supplied backend origin.
async function tenderRequest<T>(
  path: string,
  init: RequestInit,
  upload = false,
  responseError?: (response: Response) => Promise<Error>,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(
      new Request(`${window.location.origin}/tendercite-api/api/v1/${path}`, {
        ...init,
        cache: 'no-store',
        credentials: 'omit',
      }),
    );
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new Error(
      upload
        ? 'Cannot reach TenderCite. Upload outcome unknown; check the document list before retrying.'
        : 'TenderCite unavailable. Check the configured backend and development proxy.',
      { cause: error },
    );
  }
  if (!response.ok && responseError) throw await responseError(response);
  if (!response.ok && upload) throw new Error(uploadError(response.status));
  if (!response.ok)
    throw new Error(
      response.status === 404
        ? 'TenderCite record not found.'
        : `TenderCite request failed (HTTP ${response.status}).`,
    );
  try {
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error();
    return (await response.json()) as T;
  } catch {
    throw new Error(
      upload
        ? 'Invalid upload response. Upload outcome unknown; check the document list before retrying.'
        : 'TenderCite returned an invalid response. Check the development proxy.',
    );
  }
}

export interface TenderPage {
  document_id: string;
  page_number: number;
  text: string;
}

// Validate page identity as well as shape: never substitute a different cited source.
export async function tenderPage(
  documentId: string,
  pageNumber: number,
  signal: AbortSignal,
): Promise<TenderPage> {
  const page = await tenderGet<unknown>(
    `documents/${encodeURIComponent(documentId)}/pages/${pageNumber}`,
    signal,
  );
  if (
    !page ||
    typeof page !== 'object' ||
    !('document_id' in page) ||
    page.document_id !== documentId ||
    !('page_number' in page) ||
    page.page_number !== pageNumber ||
    !('text' in page) ||
    typeof page.text !== 'string'
  ) {
    throw new Error('TenderCite returned an invalid source page response.');
  }
  return { document_id: documentId, page_number: pageNumber, text: page.text };
}

export function tenderGet<T>(
  path: string,
  signal: AbortSignal,
  responseError?: (response: Response) => Promise<Error>,
): Promise<T> {
  return tenderRequest<T>(path, { signal }, false, responseError);
}

function uploadError(status: number): string {
  if (status === 413) return 'PDF exceeds the backend upload size limit.';
  if (status === 415) return 'The backend accepts supported PDF files only.';
  if (status === 422)
    return 'PDF could not be parsed. Check for encryption, damage or missing extractable text.';
  if (status === 503)
    return 'Upload or indexing unavailable. The PDF may already be stored; check the document list before retrying.';
  return `Upload failed (HTTP ${status}). Check the document list before retrying; the outcome may be uncertain.`;
}

export async function uploadTenderPdf(file: File): Promise<TenderDocument> {
  if (
    !/\.pdf$/i.test(file.name) ||
    !['', 'application/pdf', 'application/octet-stream'].includes(file.type)
  )
    throw new Error('Select PDF files only.');
  if (file.size === 0) throw new Error('The selected PDF is empty.');
  const body = new FormData();
  body.append('file', file);
  // The browser supplies the multipart boundary. Never follow an upload redirect.
  const doc = await tenderRequest<unknown>(
    'documents',
    { method: 'POST', body, redirect: 'error' },
    true,
  );
  if (
    !doc ||
    typeof doc !== 'object' ||
    !('id' in doc) ||
    typeof doc.id !== 'string' ||
    !doc.id ||
    !('filename' in doc) ||
    typeof doc.filename !== 'string' ||
    !('sha256' in doc) ||
    typeof doc.sha256 !== 'string' ||
    !/^[a-f0-9]{64}$/i.test(doc.sha256) ||
    !('page_count' in doc) ||
    typeof doc.page_count !== 'number' ||
    !Number.isSafeInteger(doc.page_count) ||
    doc.page_count < 1 ||
    !('chunk_count' in doc) ||
    typeof doc.chunk_count !== 'number' ||
    !Number.isSafeInteger(doc.chunk_count) ||
    doc.chunk_count < 0 ||
    !('created_at' in doc) ||
    typeof doc.created_at !== 'string'
  ) {
    throw new Error(
      'Invalid upload response. Upload outcome unknown; check the document list before retrying.',
    );
  }
  return {
    id: doc.id,
    filename: doc.filename,
    sha256: doc.sha256,
    page_count: doc.page_count,
    chunk_count: doc.chunk_count,
    created_at: doc.created_at,
  };
}

// Fixed intake endpoints only; share the existing local proxy and transport safeguards.
export function tenderIntakePost(
  operation: 'validate' | 'commit',
  body: string,
  responseError: (response: Response) => Promise<Error>,
): Promise<unknown> {
  return tenderRequest(
    `curated-intakes/${operation}`,
    {
      method: 'POST',
      body,
      headers: { 'Content-Type': 'application/json' },
      redirect: 'error',
    },
    false,
    responseError,
  );
}

// Review writes use the same fixed local proxy; never follow redirects or retry.
export function tenderReviewPost(
  findingId: string,
  body: string,
  responseError: (response: Response) => Promise<Error>,
): Promise<unknown> {
  return tenderRequest(
    `findings/${encodeURIComponent(findingId)}/reviews`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      redirect: 'error',
    },
    false,
    responseError,
  );
}

// Fixed assessment endpoint; no browser-selected origin, redirects or mutation retries.
export function tenderAssessmentPatch(
  findingId: string,
  body: string,
  responseError: (response: Response) => Promise<Error>,
): Promise<unknown> {
  return tenderRequest(
    `go-no-go/${encodeURIComponent(findingId)}`,
    { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body, redirect: 'error' },
    false,
    responseError,
  );
}

// Raw snapshot bytes only: the server must see duplicate keys and original whitespace.
export function tenderSnapshotPost(
  operation: 'validate' | 'import',
  body: ArrayBuffer,
  responseError: (response: Response) => Promise<Error>,
): Promise<unknown> {
  return tenderRequest(
    `capability-snapshots/${operation}`,
    {
      method: 'POST',
      body,
      headers: {
        'Content-Type': 'application/json',
        ...(operation === 'import'
          ? { 'X-TenderCite-Acknowledge-Local-Synthetic-Data': 'true' }
          : {}),
      },
      redirect: 'error',
    },
    false,
    responseError,
  );
}

export function tenderMappingPost(
  operation: 'drafts' | 'approve',
  body: string,
  responseError: (response: Response) => Promise<Error>,
  mappingId?: string,
): Promise<unknown> {
  return tenderRequest(
    operation === 'drafts'
      ? 'capability-mappings/drafts'
      : `capability-mappings/${encodeURIComponent(mappingId!)}/approve`,
    { method: 'POST', body, headers: { 'Content-Type': 'application/json' }, redirect: 'error' },
    false,
    responseError,
  );
}
