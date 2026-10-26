import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { uploadTenderPdf, type TenderDocument } from '../api/tendercite';

type UploadEntry = {
  id: number;
  filename: string;
  state: 'Queued' | 'Uploading' | 'Accepted' | 'Failed';
  document?: TenderDocument;
  error?: string;
};

export function TenderUpload() {
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const running = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const nextId = useRef(0);
  const [files, setFiles] = useState<File[]>([]);
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState({ completed: 0, total: 0 });

  async function upload() {
    if (running.current || !files.length) return;
    running.current = true;
    setBusy(true);
    const batch = files.map((file) => ({ file, id: nextId.current++ }));
    setEntries((previous) => [
      ...previous,
      ...batch.map(({ file, id }) => ({ id, filename: file.name, state: 'Queued' as const })),
    ]);
    setFiles([]);
    if (input.current) input.current.value = '';
    setProgress({ completed: 0, total: batch.length });
    try {
      for (const [index, { file, id }] of batch.entries()) {
        if (!mounted.current) break;
        const update = (change: Partial<UploadEntry>) =>
          setEntries((previous) =>
            previous.map((entry) => (entry.id === id ? { ...entry, ...change } : entry)),
          );
        update({ state: 'Uploading' });
        try {
          const document = await uploadTenderPdf(file);
          update({ state: 'Accepted', document });
        } catch (error) {
          update({
            state: 'Failed',
            error:
              error instanceof Error
                ? error.message
                : 'Upload failed. Check the document list before retrying.',
          });
        }
        // Also refresh uncertain outcomes: indexing can fail after document persistence.
        await client.cancelQueries({ queryKey: ['tendercite', 'documents'] });
        await client.invalidateQueries({ queryKey: ['tendercite', 'documents'] });
        setProgress({ completed: index + 1, total: batch.length });
      }
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="tender-upload-heading">
      <h2 id="tender-upload-heading">Upload tender PDFs</h2>
      <p>
        Select permitted local PDFs and explicitly upload them to your configured TenderCite
        backend. Identical PDFs reuse the existing document; acceptance does not mean a new document
        was created.
      </p>
      <p>
        Indexing and search may be unavailable when retrieval is disabled. This workspace does not
        enable retrieval or call an LLM.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void upload();
        }}
      >
        <label>
          PDF files
          <input
            ref={input}
            type="file"
            accept=".pdf,application/pdf"
            multiple
            disabled={busy}
            onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
          />
        </label>
        <button type="submit" disabled={busy || files.length === 0}>
          Upload
        </button>
      </form>
      {progress.total > 0 && (
        <div role="status">
          <p>
            {busy ? 'Uploading' : 'Upload batch complete'} · {progress.completed} of{' '}
            {progress.total} files processed
          </p>
          <progress aria-label="Files processed" value={progress.completed} max={progress.total} />
        </div>
      )}
      <ul aria-label="Upload results">
        {entries.map((entry) => (
          <li key={entry.id}>
            <strong>{entry.filename}</strong> · {entry.state}
            {entry.document && (
              <>
                {' '}
                ·{' '}
                <Link to={`/tenders/documents/${encodeURIComponent(entry.document.id)}`}>
                  Stored document {entry.document.id}
                </Link>
              </>
            )}
            {entry.error && <p role="alert">{entry.error}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
