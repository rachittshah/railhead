/* RailheadBridge — deterministic lockstep RPC between Python (Admin Port) and the game.
 *
 * Invariants
 *  - Inputs only ever land while the game is paused (GS pauses on start and after every step),
 *    so the tick at which every command executes is a pure function of the action log.
 *  - `ticks` counts unpaused game ticks since game start; it is persisted in Save() and is the
 *    replay clock (frame numbers reset on load and advance while paused, so they are not).
 *  - Every request gets exactly one final reply {t:"r", id, ok, ...}; list replies stream
 *    {t:"p", id, rows:[...]} chunks first.
 */

require("lib/proto.nut");
require("lib/query.nut");
require("lib/action.nut");

class RailheadBridge extends GSController {
	PROTOCOL = 1;

	ticks = 0;          // unpaused ticks since game start (replay clock)
	company = -1;       // CompanyID the model plays (-1 until bound)
	step_id = -1;       // request id of the in-flight step, -1 if none
	step_mode = 0;      // 0 none, 1 ticks, 2 economy-days
	step_left = 0;
	step_target = 0;
	loaded = false;

	constructor() {}

	function Save() {
		return { ticks = this.ticks, company = this.company };
	}

	function Load(version, data) {
		if ("ticks" in data) this.ticks = data.ticks;
		if ("company" in data) this.company = data.company;
		this.loaded = true;
	}

	function Start() {
		if (!GSGame.IsPaused()) GSGame.Pause();
		while (true) {
			this.Tick();
			GSController.Sleep(1);
		}
	}

	function Tick() {
		if (this.step_mode != 0) {
			if (!GSGame.IsPaused()) this.CountTick();
		} else if (!GSGame.IsPaused()) {
			// Running outside a step (should only happen before the first Pause lands).
			this.ticks++;
		}
		this.HandleEvents();
	}

	/* ---------------------------------------------------------------- stepping */
	function CountTick() {
		this.ticks++;
		local done = false;
		if (this.step_mode == 1) {
			this.step_left--;
			done = this.step_left <= 0;
		} else {
			done = GSDate.GetCurrentDate() >= this.step_target;
		}
		if (done) this.FinishStep();
	}

	function FinishStep() {
		local id = this.step_id;
		this.step_mode = 0;
		this.step_id = -1;
		GSGame.Pause();
		Proto.Ok(id, this.StateTable());
	}

	function H_step(id, a) {
		if (this.step_mode != 0) return Proto.Err(id, "ERR_BUSY", "a step is already running");
		if ("ticks" in a) {
			if (a.ticks < 1) return Proto.Err(id, "ERR_BAD_ARG", "ticks must be >= 1");
			this.step_mode = 1;
			this.step_left = a.ticks;
		} else if ("days" in a) {
			if (a.days < 1) return Proto.Err(id, "ERR_BAD_ARG", "days must be >= 1");
			this.step_mode = 2;
			this.step_target = GSDate.GetCurrentDate() + a.days;
		} else {
			return Proto.Err(id, "ERR_BAD_ARG", "step needs ticks or days");
		}
		this.step_id = id;
		GSGame.Unpause();
		// We resume inside the first unpaused tick: count it now.
		if (!GSGame.IsPaused()) this.CountTick();
	}

	/* ---------------------------------------------------------------- events & dispatch */
	function HandleEvents() {
		while (GSEventController.IsEventWaiting()) {
			local ev = GSEventController.GetNextEvent();
			if (ev == null) break;
			local et = ev.GetEventType();
			if (et == GSEvent.ET_ADMIN_PORT) {
				local req = GSEventAdminPort.Convert(ev).GetObject();
				if (req != null) this.Dispatch(req);
			} else {
				Events.Forward(ev, et);
			}
		}
	}

	function Dispatch(req) {
		if (typeof req != "table" || !("id" in req) || !("m" in req)) return;
		local id = req.id;
		local a = ("a" in req && req.a != null) ? req.a : {};
		local name = "H_" + req.m;
		// NB: handlers are invoked directly (never via .call/acall): commands suspend the VM and
		// Squirrel cannot suspend through a native call frame (the script would die silently).
		local where = (name in this) ? 0 : (name in Query) ? 1 : (name in Action) ? 2 : -1;
		if (where < 0) return Proto.Err(id, "ERR_UNKNOWN_METHOD", "unknown method " + req.m);
		if (this.step_mode != 0 && where == 2) {
			return Proto.Err(id, "ERR_BUSY", "world is stepping; only queries are allowed");
		}
		try {
			if (where == 0) this[name](id, a);
			else if (where == 1) Query[name](this, id, a);
			else Action[name](this, id, a);
		} catch (e) {
			Proto.Err(id, "ERR_EXCEPTION", "" + e);
		}
	}

	/* ---------------------------------------------------------------- meta */
	function StateTable() {
		return {
			date = GSDate.GetCurrentDate(),
			paused = GSGame.IsPaused(),
			ticks = this.ticks,
			company = this.company,
			stepping = this.step_mode != 0
		};
	}

	function H_hello(id, a) {
		Proto.Ok(id, { v = this.PROTOCOL, loaded = this.loaded, company = this.company, ticks = this.ticks });
	}

	function H_ping(id, a) { Proto.Ok(id, "pong"); }

	/* Diagnostics: reply with a string of n 'x' (exercises large admin packets). */
	function H_echo(id, a) {
		local n = ("n" in a) ? a.n : 1;
		local s = "x";
		while (s.len() < n) s += s;
		Proto.Ok(id, s.slice(0, n));
	}
	function H_state(id, a) { Proto.Ok(id, this.StateTable()); }

	/* Loan is only readable in that company's mode (GSCompany.GetLoanAmount takes no company). */
	static function LoanOf(c) {
		local cm = GSCompanyMode(c);
		return GSCompany.GetLoanAmount();
	}

	function H_companies(id, a) {
		local rows = [];
		for (local c = GSCompany.COMPANY_FIRST; c <= GSCompany.COMPANY_LAST; c++) {
			if (GSCompany.ResolveCompanyID(c) == GSCompany.COMPANY_INVALID) continue;
			rows.append({
				id = c,
				name = GSCompany.GetName(c),
				money = GSCompany.GetBankBalance(c),
				loan = RailheadBridge.LoanOf(c),
				value = GSCompany.GetQuarterlyCompanyValue(c, GSCompany.CURRENT_QUARTER),
				bound = (c == this.company)
			});
		}
		Proto.Rows(id, rows);
	}

	/* Deterministic digest of economically relevant state (replay / fork fidelity checks). */
	function H_checksum(id, a) {
		local h = 17;
		local mix = function(h, v) { return ((h * 1000003) ^ (v & 0xFFFFFFFF)) & 0x7FFFFFFFFFFF; };
		h = mix(h, GSDate.GetCurrentDate());
		h = mix(h, this.ticks);
		for (local c = GSCompany.COMPANY_FIRST; c <= GSCompany.COMPANY_LAST; c++) {
			if (GSCompany.ResolveCompanyID(c) == GSCompany.COMPANY_INVALID) continue;
			h = mix(h, c);
			h = mix(h, GSCompany.GetBankBalance(c));
			h = mix(h, RailheadBridge.LoanOf(c));
		}
		local vl = GSVehicleList();
		vl.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (v, _ in vl) {
			h = mix(h, v);
			h = mix(h, GSVehicle.GetLocation(v));
			h = mix(h, GSVehicle.GetState(v));
			h = mix(h, GSVehicle.GetProfitThisYear(v));
		}
		local tl = GSTownList();
		tl.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (t, _ in tl) h = mix(h, GSTown.GetPopulation(t));
		local il = GSIndustryList();
		il.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (i, _ in il) h = mix(h, GSIndustry.GetLocation(i));
		Proto.Ok(id, { h = h, date = GSDate.GetCurrentDate(), ticks = this.ticks, vehicles = vl.Count() });
	}

	function H_bind_company(id, a) {
		if (!("company" in a)) return Proto.Err(id, "ERR_BAD_ARG", "company required");
		if (GSCompany.ResolveCompanyID(a.company) == GSCompany.COMPANY_INVALID) {
			return Proto.Err(id, "ERR_NO_COMPANY", "company " + a.company + " does not exist");
		}
		this.company = a.company;
		Proto.Ok(id, { company = this.company });
	}
}
