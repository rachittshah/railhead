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

require("lib/util.nut");
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
	step_start_date = 0;
	step_until = null;   // table kind -> arg (int or null) while a step is running
	last_day = -1;
	last_month = -1;
	loaded = false;
	monitors = null;     // objective ledger: [{id, kind, cargo, target, company, total}]

	/* Wake conditions a step may stop on. GS events + conditions synthesized at day boundaries. */
	static UNTIL_KINDS = ["vehicle_crashed", "industry_open", "industry_close", "company_bankrupt",
		"company_in_trouble", "station_first_vehicle", "cash_below", "new_month"];

	constructor() { this.monitors = []; }

	function Save() {
		return { ticks = this.ticks, company = this.company, monitors = this.monitors, actions = Action.SaveState() };
	}

	function Load(version, data) {
		if ("ticks" in data) this.ticks = data.ticks;
		if ("company" in data) this.company = data.company;
		if ("monitors" in data) this.monitors = data.monitors;
		if ("actions" in data) Action.LoadState(data.actions);
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
		local today = GSDate.GetCurrentDate();
		if (today != this.last_day) {
			this.last_day = today;
			this.PollMonitors();
			local reason = this.DayConditions(today);
			if (reason != null) return this.FinishStep(reason);
		}
		local done = false;
		if (this.step_mode == 1) {
			this.step_left--;
			done = this.step_left <= 0;
		} else {
			done = today >= this.step_target;
		}
		if (done) this.FinishStep("elapsed");
	}

	/* Conditions evaluated once per game day while stepping. */
	function DayConditions(today) {
		if (this.step_until == null) return null;
		if ("cash_below" in this.step_until && this.company >= 0) {
			if (GSCompany.GetBankBalance(this.company) < this.step_until.cash_below) return "cash_below";
		}
		local m = GSDate.GetMonth(today);
		if ("new_month" in this.step_until && this.last_month >= 0 && m != this.last_month) {
			this.last_month = m;
			return "new_month";
		}
		this.last_month = m;
		return null;
	}

	function FinishStep(reason) {
		local id = this.step_id;
		this.step_mode = 0;
		this.step_id = -1;
		this.step_until = null;
		GSGame.Pause();
		local st = this.StateTable();
		st.reason <- reason;
		st.days <- st.date - this.step_start_date;
		Proto.Ok(id, st);
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
		this.step_until = null;
		if ("until" in a && a.until != null) {
			if (typeof a.until != "array") { this.step_mode = 0; return Proto.Err(id, "ERR_BAD_ARG", "until must be a list"); }
			local u = {};
			foreach (item in a.until) {
				local kind = item;
				local arg = null;
				local colon = item.find(":");
				if (colon != null) { kind = item.slice(0, colon); arg = item.slice(colon + 1).tointeger(); }
				local known = false;
				foreach (k in RailheadBridge.UNTIL_KINDS) if (k == kind) known = true;
				if (!known) { this.step_mode = 0; return Proto.Err(id, "ERR_BAD_ARG", "unknown until kind '" + kind + "'"); }
				u[kind] <- arg;
			}
			this.step_until = u;
		}
		this.step_id = id;
		this.step_start_date = GSDate.GetCurrentDate();
		this.last_day = this.step_start_date;
		this.last_month = GSDate.GetMonth(this.step_start_date);
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
				local kind = Events.Forward(ev, et);
				if (kind != null && this.step_mode != 0 && this.step_until != null && (kind in this.step_until)) {
					this.FinishStep(kind);
				}
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
		local d = GSDate.GetCurrentDate();
		return {
			ymd = GSDate.GetYear(d) + "-" + Util.Pad2(GSDate.GetMonth(d)) + "-" + Util.Pad2(GSDate.GetDayOfMonth(d)),
			date = d,
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

	/* ---------------------------------------------------------------- objective ledger
	 * Engine-native GSCargoMonitor amounts (delta since last call) accumulated daily into totals
	 * that live in the GS savegame data. Rewards are read from here during replay. */
	static MONITOR_KINDS = ["industry_delivery", "industry_pickup", "town_delivery", "town_pickup"];

	function PollMonitors() {
		foreach (m in this.monitors) {
			local d = 0;
			if (m.kind == "industry_delivery") d = GSCargoMonitor.GetIndustryDeliveryAmount(m.company, m.cargo, m.target, true);
			else if (m.kind == "industry_pickup") d = GSCargoMonitor.GetIndustryPickupAmount(m.company, m.cargo, m.target, true);
			else if (m.kind == "town_delivery") d = GSCargoMonitor.GetTownDeliveryAmount(m.company, m.cargo, m.target, true);
			else if (m.kind == "town_pickup") d = GSCargoMonitor.GetTownPickupAmount(m.company, m.cargo, m.target, true);
			if (d > 0) m.total += d;
		}
	}

	function H_monitor_add(id, a) {
		local kind = ("kind" in a) ? a.kind : null;
		local known = false;
		foreach (k in RailheadBridge.MONITOR_KINDS) if (k == kind) known = true;
		if (!known) return Proto.Err(id, "ERR_BAD_ARG", "kind must be one of industry_delivery, industry_pickup, town_delivery, town_pickup");
		if (!("cargo" in a) || !GSCargo.IsValidCargo(a.cargo)) return Proto.Err(id, "ERR_BAD_ARG", "invalid cargo");
		if (!("target" in a)) return Proto.Err(id, "ERR_BAD_ARG", "target required");
		local is_town = kind.find("town") == 0;
		if (is_town ? !GSTown.IsValidTown(a.target) : !GSIndustry.IsValidIndustry(a.target)) {
			return Proto.Err(id, "ERR_BAD_ARG", "invalid target " + a.target);
		}
		local company = ("company" in a) ? a.company : this.company;
		if (company < 0) return Proto.Err(id, "ERR_NO_COMPANY", "no company bound");
		local m = { id = this.monitors.len(), kind = kind, cargo = a.cargo, target = a.target, company = company, total = 0 };
		this.monitors.append(m);
		this.PollMonitors();  // starts monitoring (first call) without counting history
		m.total = 0;
		Proto.Ok(id, m.id);
	}

	function H_monitor_read(id, a) {
		this.PollMonitors();
		Proto.Rows(id, this.monitors);
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
