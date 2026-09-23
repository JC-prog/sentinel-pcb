import json
import os
import threading
import traceback
from dataclasses import asdict
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Make direct UI launch resolve both project and Agent 1 imports.
import sys
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _path in (_PROJECT_ROOT, _PROJECT_ROOT / "src" / "agent1_orchestrator"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from adc_shared.client import DataClient
from agents.orchestrator import OrchestratorAgent
from services.dataset_preparation import DatasetPreparationService
from services.dataset_verification import DatasetVerificationService


class ADCApplication(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Agentic ADC – LLM Orchestrator")
        self.geometry("1120x820")
        self.minsize(1000, 720)

        self.project_root = Path(__file__).resolve().parents[2]
        self.dataset_var = tk.StringVar()
        self.xml_var = tk.StringVar()
        self.image_root_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(self.project_root / "outputs" / "result.json"))

        self.feature_threshold_var = tk.DoubleVar(value=0.70)
        self.defect_threshold_var = tk.DoubleVar(value=0.70)
        self.use_llm_var = tk.BooleanVar(value=False)
        self.llm_model_var = tk.StringVar(value=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"))
        self.llm_fallback_var = tk.BooleanVar(value=True)

        self.status_var = tk.StringVar(value="Ready")
        self.input_count_var = tk.StringVar(value="0")
        self.prepared_var = tk.StringVar(value="0")
        self.verified_var = tk.StringVar(value="0")
        self.inference_var = tk.StringVar(value="0")
        self.accepted_var = tk.StringVar(value="0")
        self.review_var = tk.StringVar(value="0")

        self._build_ui()

    def _build_ui(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="Agentic ADC – LLM Planning + Deterministic Policy + Two-Stage Inference",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w", pady=(0, 4))

        ttk.Label(
            outer,
            text=(
                "ADC Inputs → Workflow State → LLM Planner → Observation / Constraint / Decision / Reason "
                "→ Policy Engine → Tool Execution → State Update → Re-plan"
            ),
        ).pack(anchor="w", pady=(0, 14))

        input_box = ttk.LabelFrame(outer, text="Input Selection", padding=12)
        input_box.pack(fill="x")
        self._file_row(input_box, 0, "Dataset CSV", self.dataset_var, self._browse_dataset)
        self._file_row(input_box, 1, "Inspection XML", self.xml_var, self._browse_xml)
        self._file_row(input_box, 2, "Image Folder (Optional)", self.image_root_var, self._browse_image_root)
        self._file_row(input_box, 3, "Output JSON", self.output_var, self._browse_output)
        ttk.Label(
            input_box,
            text=(
                "Image Folder remaps paths below the original 'usi' root. Example: "
                "...\\usi\\35-... → D:\\Image\\35-..."
            ),
            foreground="#555555",
        ).grid(row=4, column=1, sticky="w", padx=(4, 8), pady=(0, 4))

        options = ttk.LabelFrame(outer, text="Policy + LLM Planner", padding=12)
        options.pack(fill="x", pady=(12, 0))

        ttk.Label(options, text="Feature confidence threshold").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(
            options, from_=0.0, to=1.0, increment=0.05,
            textvariable=self.feature_threshold_var, width=9
        ).grid(row=0, column=1, sticky="w", padx=(8, 26))

        ttk.Label(options, text="Defect confidence threshold").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(
            options, from_=0.0, to=1.0, increment=0.05,
            textvariable=self.defect_threshold_var, width=9
        ).grid(row=0, column=3, sticky="w", padx=(8, 26))

        ttk.Checkbutton(
            options,
            text="Use real LLM Planner",
            variable=self.use_llm_var,
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))

        ttk.Label(options, text="OpenAI model").grid(row=1, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(options, textvariable=self.llm_model_var, width=24).grid(
            row=1, column=3, sticky="w", padx=(8, 26), pady=(10, 0)
        )

        ttk.Checkbutton(
            options,
            text="Fallback to deterministic planner if LLM call fails",
            variable=self.llm_fallback_var,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        api_state = "OPENAI_API_KEY detected" if os.getenv("OPENAI_API_KEY") else "OPENAI_API_KEY not detected"
        ttk.Label(options, text=api_state, foreground="#555555").grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

        button_bar = ttk.Frame(outer)
        button_bar.pack(fill="x", pady=14)
        self.prepare_btn = ttk.Button(button_bar, text="1. Prepare", command=self._prepare_async)
        self.prepare_btn.pack(side="left")
        self.verify_btn = ttk.Button(button_bar, text="2. Prepare + Verify", command=self._verify_async)
        self.verify_btn.pack(side="left", padx=(8, 0))
        self.run_btn = ttk.Button(button_bar, text="3. Run Agentic Workflow", command=self._run_async)
        self.run_btn.pack(side="left", padx=(8, 0))
        ttk.Button(button_bar, text="Clear Log", command=self._clear_log).pack(side="right")

        summary_box = ttk.LabelFrame(outer, text="Workflow Summary", padding=10)
        summary_box.pack(fill="x")
        labels = [
            ("Status", self.status_var),
            ("Input", self.input_count_var),
            ("Prep Ready", self.prepared_var),
            ("Verified", self.verified_var),
            ("Inference", self.inference_var),
            ("Accepted", self.accepted_var),
            ("Review", self.review_var),
        ]
        for i, (name, var) in enumerate(labels):
            ttk.Label(summary_box, text=f"{name}:").grid(row=0, column=i * 2, sticky="w")
            ttk.Label(
                summary_box,
                textvariable=var,
                font=("Segoe UI", 10, "bold") if name == "Status" else None,
            ).grid(row=0, column=i * 2 + 1, sticky="w", padx=(5, 18))

        log_box = ttk.LabelFrame(outer, text="Execution Log / Planner History / Results", padding=8)
        log_box.pack(fill="both", expand=True, pady=(12, 0))
        self.log = tk.Text(log_box, wrap="word", font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._log(
            "Agent loop:\n"
            "State → Planner → Policy → Tool → State Update → Re-plan\n"
            "Enable 'Use real LLM Planner' to make the next-action decision through OpenAI.\n\n"
        )

    def _file_row(self, parent, row, label, variable, command):
        ttk.Label(parent, text=label, width=22).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(4, 8), pady=5)
        ttk.Button(parent, text="Browse...", command=command, width=12).grid(row=row, column=2, pady=5)
        parent.columnconfigure(1, weight=1)

    def _browse_dataset(self):
        path = filedialog.askopenfilename(title="Select dataset.csv", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.dataset_var.set(path)

    def _browse_xml(self):
        path = filedialog.askopenfilename(title="Select AOI Inspection XML", filetypes=[("XML files", "*.xml"), ("All files", "*.*")])
        if path:
            self.xml_var.set(path)

    def _browse_image_root(self):
        path = filedialog.askdirectory(title="Select new image root folder")
        if path:
            self.image_root_var.set(path)

    def _browse_output(self):
        path = filedialog.asksaveasfilename(title="Select output JSON", defaultextension=".json", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.output_var.set(path)

    def _validate_inputs(self):
        dataset = Path(self.dataset_var.get().strip())
        xml = Path(self.xml_var.get().strip())
        if not dataset.is_file():
            messagebox.showerror("Input Error", "Please select a valid dataset CSV file.")
            return None
        if not xml.is_file():
            messagebox.showerror("Input Error", "Please select a valid inspection XML file.")
            return None
        image_root_text = self.image_root_var.get().strip()
        image_root = image_root_text if image_root_text else None
        return str(dataset), str(xml), image_root

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.prepare_btn.configure(state=state)
        self.verify_btn.configure(state=state)
        self.run_btn.configure(state=state)

    def _run_background(self, target, extra=()):
        inputs = self._validate_inputs()
        if not inputs:
            return
        self._set_busy(True)
        self.status_var.set("Running...")
        threading.Thread(target=self._safe_worker, args=(target, inputs + tuple(extra)), daemon=True).start()

    def _safe_worker(self, target, args):
        try:
            target(*args)
        except Exception:
            error = traceback.format_exc()
            self.after(0, lambda: self._log(error))
            self.after(0, lambda: self.status_var.set("ERROR"))
        finally:
            self.after(0, lambda: self._set_busy(False))

    def _prepare_async(self):
        self._run_background(self._prepare)

    def _verify_async(self):
        self._run_background(self._verify)

    def _run_async(self):
        # Capture Tk variables on the UI thread before starting worker thread.
        settings = (
            float(self.feature_threshold_var.get()),
            float(self.defect_threshold_var.get()),
            bool(self.use_llm_var.get()),
            self.llm_model_var.get().strip() or "gpt-5.6-luna",
            bool(self.llm_fallback_var.get()),
            self.output_var.get().strip(),
        )
        self._run_background(self._run_full, settings)

    def _prepare(self, dataset, xml, image_root):
        self.after(0, lambda: self._log("\n=== DATASET PREPARATION ===\n"))
        result = DatasetPreparationService().prepare(dataset, xml, image_root)
        self.after(0, lambda: self.input_count_var.set(str(result.metrics.get("total_samples", 0))))
        self.after(0, lambda: self.prepared_var.set(str(result.metrics.get("ready_samples", 0))))
        self.after(0, lambda: self.status_var.set(result.status))
        payload = {
            "status": result.status,
            "message": result.message,
            "metrics": result.metrics,
            "errors": result.errors[:30],
        }
        self.after(0, lambda: self._log(json.dumps(payload, indent=2) + "\n"))

    def _verify(self, dataset, xml, image_root):
        self.after(0, lambda: self._log("\n=== PREPARE + VERIFY ===\n"))
        prep = DatasetPreparationService().prepare(dataset, xml, image_root)
        samples = prep.data.get("samples", [])
        result = DatasetVerificationService().verify(samples)
        self.after(0, lambda: self.input_count_var.set(str(prep.metrics.get("total_samples", 0))))
        self.after(0, lambda: self.prepared_var.set(str(prep.metrics.get("ready_samples", 0))))
        self.after(0, lambda: self.verified_var.set(str(result.metrics.get("passed_samples", 0))))
        self.after(0, lambda: self.status_var.set(result.status))
        payload = {
            "preparation": {"status": prep.status, "metrics": prep.metrics},
            "verification": {
                "status": result.status,
                "metrics": result.metrics,
                "sample_results": result.data.get("sample_results", []),
            },
        }
        self.after(0, lambda: self._log(json.dumps(payload, indent=2) + "\n"))

    def _run_full(self, dataset, xml, image_root, feature_threshold, defect_threshold, use_llm, llm_model, llm_fallback, output_text):
        self.after(0, lambda: self._log("\n=== AGENTIC ADC WORKFLOW ===\n"))
        self.after(0, lambda: self._log(f"Planner: {'OpenAI LLM' if use_llm else 'Deterministic'} | Model: {llm_model if use_llm else 'N/A'}\n"))

        agent = OrchestratorAgent(
            self.project_root,
            feature_threshold=feature_threshold,
            defect_threshold=defect_threshold,
            use_llm=use_llm,
            planner_model=llm_model,
            allow_llm_fallback=llm_fallback,
            enable_a2a=False,
            populate_vector_db=False,
        )
        state = agent.run(dataset, xml, image_root)

        output = Path(output_text) if output_text else self.project_root / "outputs" / "result.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")

        # ADC_REST_PERSISTENCE_V1
        # Save the retry ID before the network call. JSON is already on disk.
        import uuid
        run_id = str(uuid.uuid4())
        output.with_suffix(".run_id.txt").write_text(run_id, encoding="utf-8")
        try:
            saved = DataClient().save_run(asdict(state), run_id=run_id)
        except Exception as exc:
            message = f"Shared DB save failed; JSON retained. Retry run ID: {run_id}\n{exc}\n"
            self.after(0, lambda message=message: self._log(message))
        else:
            message = f"Shared Qdrant save complete. Run ID: {saved['run_id']}\n"
            self.after(0, lambda message=message: self._log(message))

        self.after(0, lambda: self.status_var.set(state.status))
        self.after(0, lambda: self.input_count_var.set(str(state.input_samples)))
        self.after(0, lambda: self.prepared_var.set(str(state.preparation_ready)))
        self.after(0, lambda: self.verified_var.set(str(state.verification_passed)))
        self.after(0, lambda: self.inference_var.set(str(state.inference_attempted)))
        self.after(0, lambda: self.accepted_var.set(str(state.accepted)))
        self.after(0, lambda: self.review_var.set(str(state.review_required)))

        summary = {
            "workflow_status": state.status,
            "termination_reason": state.termination_reason,
            "planner_backend": state.planner_backend,
            "planner_model": state.planner_model,
            "input_samples": state.input_samples,
            "preparation_ready": state.preparation_ready,
            "preparation_failed": state.preparation_failed,
            "verification_passed": state.verification_passed,
            "verification_failed": state.verification_failed,
            "inference_attempted": state.inference_attempted,
            "inference_completed": state.inference_completed,
            "accepted": state.accepted,
            "review_required": state.review_required,
            "inference_aborted": state.inference_aborted,
            "plan_history": state.plan_history,
            "observations": state.observations,
            "results": state.inference_results,
            "output_json": str(output),
        }
        self.after(0, lambda: self._log(json.dumps(summary, indent=2) + "\n"))
        self.after(0, lambda: messagebox.showinfo(
            "ADC Workflow Finished",
            f"Status: {state.status}\nReason: {state.termination_reason}\n\nResult saved to:\n{output}",
        ))

    def _log(self, text):
        self.log.insert("end", text)
        self.log.see("end")

    def _clear_log(self):
        self.log.delete("1.0", "end")


if __name__ == "__main__":
    ADCApplication().mainloop()
