import { FormEvent, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { startWorkflowRun } from "../api/client";

export default function RunStarter({
  workflow,
  onStarted,
}: {
  workflow: string;
  onStarted: (runId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const [expected, setExpected] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      startWorkflowRun(workflow, {
        input: input.trim(),
        expected: expected.trim() || undefined,
      }),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["runs"] }),
        queryClient.invalidateQueries({ queryKey: ["approvals"] }),
        queryClient.invalidateQueries({ queryKey: ["node-metrics", workflow] }),
      ]);
      onStarted(result.run_id);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!input.trim() || mutation.isPending) return;
    mutation.mutate();
  }

  return (
    <form className="border-b border-slate-700 p-3 space-y-2 bg-slate-900" onSubmit={submit}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs uppercase text-slate-400">Run workflow</div>
        <button
          type="submit"
          disabled={!input.trim() || mutation.isPending}
          className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white disabled:bg-slate-700 disabled:text-slate-500"
        >
          {mutation.isPending ? "Running..." : "Run"}
        </button>
      </div>
      <textarea
        className="h-20 w-full resize-none rounded border border-slate-600 bg-slate-800 p-2 text-sm text-slate-100 placeholder:text-slate-500"
        placeholder={`Input for ${workflow}`}
        value={input}
        onChange={(event) => setInput(event.target.value)}
      />
      <input
        className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-slate-100 placeholder:text-slate-500"
        placeholder="Expected output or test hint (optional)"
        value={expected}
        onChange={(event) => setExpected(event.target.value)}
      />
      {mutation.error && (
        <div className="rounded border border-red-700 bg-red-900/30 p-2 text-xs text-red-400">
          {mutation.error instanceof Error ? mutation.error.message : "Run failed"}
        </div>
      )}
      {mutation.data && (
        <div className="text-xs text-slate-400">
          Created <code>{mutation.data.run_id}</code> with status <code>{mutation.data.status}</code>.
        </div>
      )}
    </form>
  );
}
