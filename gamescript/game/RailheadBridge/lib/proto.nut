/* Reply helpers. GS->admin JSON per message is size-limited (1460 B on stock servers, larger with
 * Railhead's engine patch), so list replies stream rows in batches that halve on send failure. */
Proto <- {
	rows_per_msg = 16,

	function Ok(id, v) {
		if (!GSAdmin.Send({ t = "r", id = id, ok = true, v = v })) {
			Proto.Err(id, "ERR_SEND_FAILED", "reply too large for one admin packet");
		}
	}

	function Err(id, code, msg) {
		GSAdmin.Send({ t = "r", id = id, ok = false, code = code, msg = msg });
	}

	function SendBatch(id, batch) {
		if (batch.len() == 0) return;
		if (GSAdmin.Send({ t = "p", id = id, rows = batch })) return;
		if (batch.len() == 1) {
			GSAdmin.Send({ t = "p", id = id, rows = [{ _error = "row too large" }] });
			return;
		}
		local mid = batch.len() / 2;
		Proto.SendBatch(id, batch.slice(0, mid));
		Proto.SendBatch(id, batch.slice(mid));
	}

	/* Stream an array of rows then the terminating reply. */
	function Rows(id, rows) {
		local n = rows.len();
		local i = 0;
		while (i < n) {
			local j = i + Proto.rows_per_msg;
			if (j > n) j = n;
			Proto.SendBatch(id, rows.slice(i, j));
			i = j;
		}
		GSAdmin.Send({ t = "r", id = id, ok = true, rows = true, n = n });
	}

	function Event(kind, data) {
		data.t <- "ev";
		data.k <- kind;
		GSAdmin.Send(data);
	}
}

Events <- {
	function Forward(ev, et) {
		switch (et) {
			case GSEvent.ET_VEHICLE_CRASHED: {
				local e = GSEventVehicleCrashed.Convert(ev);
				Proto.Event("vehicle_crashed", { vehicle = e.GetVehicleID(), tile = e.GetCrashSite(), reason = e.GetCrashReason() });
				break;
			}
			case GSEvent.ET_INDUSTRY_OPEN: {
				Proto.Event("industry_open", { industry = GSEventIndustryOpen.Convert(ev).GetIndustryID() });
				break;
			}
			case GSEvent.ET_INDUSTRY_CLOSE: {
				Proto.Event("industry_close", { industry = GSEventIndustryClose.Convert(ev).GetIndustryID() });
				break;
			}
			case GSEvent.ET_COMPANY_BANKRUPT: {
				Proto.Event("company_bankrupt", { company = GSEventCompanyBankrupt.Convert(ev).GetCompanyID() });
				break;
			}
			case GSEvent.ET_COMPANY_IN_TROUBLE: {
				Proto.Event("company_in_trouble", { company = GSEventCompanyInTrouble.Convert(ev).GetCompanyID() });
				break;
			}
			case GSEvent.ET_STATION_FIRST_VEHICLE: {
				local e = GSEventStationFirstVehicle.Convert(ev);
				Proto.Event("station_first_vehicle", { station = e.GetStationID(), vehicle = e.GetVehicleID() });
				break;
			}
			default:
				break;
		}
	}
}
