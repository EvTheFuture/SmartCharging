"""
    Smart Charging of EVs based on hourly price.
    Copyright (C) 2020-2022    Magnus Sandin <magnus.sandin@gmail.com>

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.

Arguments in config file:

debug:                      O: yes | no (activate debug logging)
finish_at_latest_by:        M: Time String or entity id
price_data:                 M: Where to fetch the price info
    - entity:               M: entity_id[,attribute] to fetch price from
      required:             O: Is this required to start charging Yes | No
charger_switch:             M: Switch to start and stop EV charger
charging_state:             M: Sensor to read charging state from
charging_state_stopped:     O: String that indicate not charging
charging_state_charging:    O: String that indicate charging
charging_state_complete:    O: String that indicate charging complete
device_tracker:             O: Sensor indicating if EV is home
device_tracker_value_home:  O: Value of senor when vehicle is home (default is home)
time_left:                  M: Time left in hours (float) entity_id[,attribute]


EXAMPLE:
charge_ev_when_cheepest:
    module: ev_charge_control
    class: SmartCharging
    finish_at_latest_by: "07:30"
    price_data:
        - entity: sensor.nordpool_kwh_se3_sek_3_1000_025,raw_today
          required: Yes
        - entity: sensor.nordpool_kwh_se3_sek_3_1000_025,raw_tomorrow
          required: Yes
    charger_switch: switch.model_3_charger_switch
    charging_state: binary_sensor.model_3_charger_sensor,charging_state
    charging_state_stopped: Stopped
    charging_state_charging: Charging
    charging_state_complete: Complete
    device_tracker: device_tracker.model_3_location_tracker
    device_tracker_value_home: home
    time_left: sensor.model_3_charging_rate_sensor,time_left
    debug: yes
"""

VERSION = "0.54.3"

# Store all attributes every day to disk
STORE_TO_FILE_EVERY = 60 * 60 * 24

DELAY_AFTER_STATE_CHANGE = 5.0
MAX_TIME_FOR_WORKER_TO_SLEEP = 3600  # 1 hour
RETRY_AFTER_FAILURE = 60  # 1 minute

ENTITIES = {
    "~_active": {
        "type": "switch",
        "state": "on",
        "attributes": {
            "friendly_name": "Smart Charging Active",
        },
    },
    "~_status": {
        "type": "sensor",
        "state": "unknown",
        "attributes": {
            "friendly_name": "Smart Charging Status",
            "charge_time_left": "unknown",
            "next_start": "unknown",
            "next_stop": "unknown",
            "slots": [],
            "reason": "",
            "last_calculation": "unknown",
            "version": VERSION,
        },
    },
}
import appdaemon.plugins.hass.hassapi as hass
import copy
import json
import queue

from datetime import datetime
from datetime import timedelta
from datetime import time
from dateutil import parser
from dateutil import tz

class SmartCharging(hass.Hass):

    data = {}
    home_tag = "home"

    def initialize(self):
        if "debug" in self.args and self.args["debug"]:
            self.set_log_level("DEBUG")

        self.log("Starting....")

        self.debug(f"App pin state: {self.get_app_pin()}")
        self.debug(f"Current thread: {self.get_pin_thread()}")
        self.event_listeners = []

        self.status_state = "unknown"
        self.status_attributes = copy.deepcopy(
            ENTITIES["~_status"]["attributes"]
        )

        # This is the file where we store current states
        # between restarts for this app
        self.persistance_file = (
            __file__.rsplit("/", 1)[0] + f"/{self.name}.json"
        )

        self.load_persistance_file()

        self.initialize_entities()

        self.run_calculations_handle = None
        self.charge_time_needed = None

        self.status_complete = self.get_config_value(
            "charging_state_complete", "complete"
        ).lower()
        self.status_charging = self.get_config_value(
            "charging_state_charging", "charging"
        ).lower()
        self.status_stopped = self.get_config_value(
            "charging_state_stopped", "stopped"
        ).lower()

        self.time_when_charging_started = self.datetime(aware=True)

        self.setup_listener("switch." + self.name + "_active")
        self.setup_listener(self.args["finish_at_latest_by"])
        self.setup_listener(self.args["charger_switch"])
        self.setup_listener(self.args["charging_state"])
        self.setup_listener(self.args["device_tracker"])
        self.setup_listener(self.args["time_left"])

        for pd in self.args["price_data"]:
            self.setup_listener(pd["entity"])

        if "device_tracker_value_home" in self.args:
            self.home_tag = self.args["device_tracker_value_home"]
            self.debug(f"Setting home tag to: {self.home_tag}")

        # Save the current state of all covers every STORE_COVER_STATE_EVERY
        # seconds
        self.run_every(
            callback=self.save_persistance_file,
            start=f"now+{STORE_TO_FILE_EVERY}",
            interval=STORE_TO_FILE_EVERY,
        )

        # Trigger an initial calculation
        self.schedule_worker(1)

    def terminate(self):
        self.debug("Will terminate...")
        self.save_persistance_file()

        self.abort = True

        # Remove all event listeners
        for l in self.event_listeners:
            self.cancel_listen_event(l)

        self.debug("Finished clean up process, bye bye...")

    def load_persistance_file(self):
        """Load persistance data from file when app starts
        and initialize mandatory data if it doenst exist."""
        try:
            with open(self.persistance_file, "r") as f:
                self.data = json.load(f)

        except IOError as e:
            self.log(f"Persistance file {self.persistance_file} not found...")
            self.data = {}

        except Exception as e:
            self.get_main_log().exception(
                "Unexpected exception when loading persistance file..."
            )
            self.data = {}

        for k, v in ENTITIES.items():
            if k not in self.data:
                self.data[k] = v["state"]

    def save_persistance_file(self, kwargs=None):
        """Save persistance data to file"""
        try:
            with open(self.persistance_file, "w") as f:
                f.write(json.dumps(self.data, indent=4))

            self.log(f"Persistance entries written to {self.persistance_file}")

        except Exception as e:
            self.get_main_log().exception(
                "Unexpected exception when storing persistence file..."
            )
            return False

    def debug(self, text):
        self.get_main_log().debug(text)

    def remove_timer(self, th):
        """Wrapper to cancel_timer with sanity checks

        Parameters
        ----------
        th : str
            Timer Handle from run_in
        """
        if th is not None and self.timer_running(th):
            self.cancel_timer(th)
            self.debug(f"Cancelled the timer with handle: {th}")

    def get_friendly_date(self, in_date):
        today = self.datetime(aware=True).date().today()
        tomorrow = today + timedelta(days=1)
        i = in_date.date()

        return "Today" if i == today else "Tomorrow" if i == tomorrow else i

    def initialize_entities(self):
        self.debug("Setting up entities")

        for k, v in ENTITIES.items():
            entity_id = v["type"] + "." + k.replace("~", self.name)
            self.debug(f"Will setup entity: {entity_id} -> {v}")

            self.set_state(
                entity_id=entity_id,
                state=self.data[k],
                attributes=v["attributes"],
            )
            self.debug(f"Created entity {entity_id}")

            if v["type"] == "switch":
                self.event_listeners.append(
                    self.listen_event(
                        self.handle_incoming_event,
                        "call_service",
                        entity_id=entity_id,
                        domain="switch",
                    )
                )
                self.debug(f"Registered service call handler for {entity_id}")

    def get_entity_last_changed(self, entity):
        e, a = self.get_entity_and_attribute(entity)
        return self.get_state(entity_id=e, attribute="last_changed")

    def get_entity_value(self, entity):
        e, a = self.get_entity_and_attribute(entity)

        return self.get_state(entity_id=e, attribute=a)

    def setup_listener(self, entity):
        e, a = self.get_entity_and_attribute(entity)

        if e is None:
            self.debug(f"{entity} is not an entity, skipping listen_state")
            return

        self.log(f"Setting up listener for entity: {e}, attriute: {a}")
        self.listen_state(callback=self.new_state, entity_id=e, attribute=a)

    def handle_incoming_event(self, event_name, data, kwargs):
        try:
            if (
                event_name == "call_service"
                and "domain" in kwargs
                and "entity_id" in kwargs
            ):

                if data["domain"] == kwargs["domain"]:

                    entity_id = data["service_data"]["entity_id"]
                    if (
                        isinstance(entity_id, str)
                        and entity_id == kwargs["entity_id"]
                    ) or (
                        isinstance(entity_id, list)
                        and kwargs["entity_id"] in entity_id
                    ):
                        key = entity_id.replace("switch." + self.name, "~")

                        if key in self.data:
                            # When we set the state, self.new_state will be
                            # triggered and the actual internal state will be
                            # updated properly
                            self.set_state(
                                entity_id=kwargs["entity_id"],
                                state=data["service"].replace("turn_", ""),
                                attributes=ENTITIES[key]["attributes"],
                            )
        except Exception as e:
            self.get_main_log().exception(f"Exception when handling event")

    def new_state(self, entity, attribute, old, new, kwargs):
        self.debug(f"NEW STATE!! {entity}.{attribute} = {new} ({old})")

        run_after = DELAY_AFTER_STATE_CHANGE

        if entity.endswith(self.name + "_active"):
            self.data["~_active"] = new
            if new == "off":
                self.debug("Trigger Calculations Immedietly...")
                run_after = 0

        # Delay trigger to avoid multiple calculations when multiple entities
        # change at the same time
        self.schedule_worker(run_after)

    def schedule_worker(self, delay):
        self.remove_timer(self.run_calculations_handle)

        self.debug(f"Scheduling Calculations to run after {delay} seconds...")
        self.run_calculations_handle = self.run_in(self.worker, delay)

    def worker(self, kwargs):
        self.debug(f"Current thread: {self.get_pin_thread()}")

        try:
            self.debug("worker: Starting calculation...")
            retry = not self.calculate()
        except Exception as e:
            self.get_main_log().exception("Unexpected exception...")
            retry = True

        # Just to make sure we can't get a negative number
        # we store the time before creating the price list
        now = self.datetime(aware=True)

        self.debug(f"worker: now is: {now}")
        price = self.get_price()

        if price is not None and len(price):
            sleep_time = (price[0]["end"] - now).total_seconds()

            if sleep_time > MAX_TIME_FOR_WORKER_TO_SLEEP:
                sleep_time = MAX_TIME_FOR_WORKER_TO_SLEEP
        else:
            sleep_time = MAX_TIME_FOR_WORKER_TO_SLEEP

        if retry and sleep_time > RETRY_AFTER_FAILURE:
            sleep_time = RETRY_AFTER_FAILURE

        self.schedule_worker(sleep_time)

        self.log(
            f"worker: Done for now. Will run again in {sleep_time} seconds..."
        )

    def calculate(self):
        self.debug("Time to calculate...")
        nowstr = self.datetime(aware=True).strftime("%H:%M")

        self.debug(f"nowstr: {nowstr}")

        self.status_attributes["last_calculation"] = nowstr
        self.status_attributes["next_start"] = None
        self.status_attributes["next_stop"] = None
        self.status_attributes["slots"] = None
        self.status_attributes["charge_time_left"] = None

        self.debug(f"status: {self.status_attributes}")

        if self.data["~_active"] == "off":
            self.debug("Module is inactivated by user...")
            if self.status_state == "stopped":
                self.start_charging()
                self.debug("Since user have inactivated us, start charging...")

            self.status_state = "disabled"
            self.status_attributes["reason"] = "Disabled by user"
            self.charge_time_needed = None
            self.update_status_entity()
            return True

        if self.get_entity_value(self.args["device_tracker"]) != self.home_tag:
            self.debug("EV is not home, aborting calculation...")
            self.status_state = "inactive"
            self.status_attributes["reason"] = "EV is not home"
            self.charge_time_needed = None
            self.update_status_entity()
            return True

        self.debug("Trying to get current charging state...")
        cs = self.get_entity_value(self.args["charging_state"])
        self.debug(f"cs: {cs}")
        if cs is None:
            self.debug("Unable to read charging state...")
            self.error(f"Unable to read entity {self.args['charging_state']}")
            self.status_state = "error"
            self.status_attributes["reason"] = "Error reading 'charging_state'"
            self.update_status_entity()
            self.charge_time_needed = None
            return True

        cs = cs.lower()
        cs_lc = self.get_entity_last_changed(self.args["charging_state"])

        tl = self.get_entity_value(self.args["time_left"])
        tl_lc = self.get_entity_last_changed(self.args["time_left"])

        self.debug(f"========== cs_lc: {cs_lc} ---- tl_lc: {tl_lc}")

        time_diff = parser.parse(cs_lc) - parser.parse(tl_lc)

        self.debug(f"========== time_diff: {time_diff}")

        self.debug(f"Current state is: {cs}, time left: {tl}")

        if cs == self.status_charging:
            if tl > 0 and time_diff.total_seconds() < 0:
                self.charge_time_needed = int(tl * 3600)
            else:
                self.charge_time_needed = None

        elif cs == self.status_complete:
            self.status_state = "complete"
            self.status_attributes["reason"] = "EV is charged"
            self.status_attributes["charge_time_left"] = self.format_time(0)
            self.charge_time_needed = None
            self.update_status_entity()
            # Just in case we charged to little
            self.start_charging()
            return True
        elif cs == self.status_stopped and self.status_state == "calculating":
            # Make sure we actually get time left
            self.charge_time_needed = None
            self.start_charging()
            return True

        if self.charge_time_needed is not None:
            self.status_attributes["charge_time_left"] = self.format_time(
                self.charge_time_needed
            )
        else:
            self.status_attributes["charge_time_left"] = "unknown"

        return self.start_stop_charging()

    def format_time(self, seconds):
        h = int(seconds / 3600)
        m = int(seconds / 60 - h * 60)

        return f"{h:02}:{m:02}"

    def start_charging(self):
        try:
            self.call_service(
                "homeassistant/turn_on", entity_id=self.args["charger_switch"]
            )
            return True

        except Exception as e:
            self.get_main_log().exception(
                "Unexpected exception when calling service..."
            )
            return False

    def stop_charging(self):
        try:
            self.call_service(
                "homeassistant/turn_off", entity_id=self.args["charger_switch"]
            )
            return True

        except Exception as e:
            self.get_main_log().exception(
                "Unexpected exception when calling service..."
            )
            return False

    def start_stop_charging(self):
        self.debug("Entering start_stop_charging...")

        if self.charge_time_needed is None:
            self.log("Starting to charge to calculate time needed")
            self.status_attributes["charge_time_left"] = "unknown"
            self.status_attributes["next_start"] = ""
            self.status_attributes["next_stop"] = ""
            self.status_attributes["slots"] = []

            if not self.start_charging():
                self.status_state = "error"
                self.status_attributes[
                    "reason"
                ] = "Unable to communicate with EV"
                self.update_status_entity()
                return False
            else:
                self.status_state = "calculating"
                self.status_attributes["reason"] = "Asking EV for time left"
                self.update_status_entity()
                return True

        price = self.get_price()
        if price is None:
            self.log("We don't have required prices, aborting...")
            self.status_attributes["next_start"] = ""
            self.status_attributes["next_stop"] = ""
            self.status_attributes["slots"] = []

            if not self.stop_charging():
                self.status_state = "error"
                self.status_attributes[
                    "reason"
                ] = "Unable to communicate with EV"
                self.update_status_entity()
                return False
            else:
                self.status_state = "stopped"
                self.status_attributes["reason"] = "Missing price info"
                self.update_status_entity()
                return True

        self.debug(f"We need {self.charge_time_needed} seconds to charge")

        current_slot = price[0]
        price = sorted(price, key=lambda i: i["price"])

        self.debug(f"Valid prices: {len(price)}")

        # Find cheapest slots
        slots = []
        length = 0
        for p in price:
            slots.append(p)
            length += p["length"]

            if length > self.charge_time_needed:
                break

        if not len(slots):
            self.log("We don't need any slots...")
            self.status_state = "no slots"
            self.status_attributes["reason"] = "No slots needed"
            self.status_attributes["next_start"] = ""
            self.status_attributes["next_stop"] = ""
            self.status_attributes["slots"] = []
            self.update_status_entity()
            return self.start_charging()

        # Make sure we have the slots in time order
        slots = sorted(slots, key=lambda i: i["start"])

        self.debug(f"WE NEED THESE SLOTS: {slots} ({length})")

        friendly_slots = []
        for s in slots:
            slot = {
                "start": (
                    self.get_friendly_date(s["start"])
                    + " at "
                    + s["start"].strftime("%H:%M")
                ),
                "end": (
                    self.get_friendly_date(s["end"])
                    + " at "
                    + s["end"].strftime("%H:%M")
                ),
                "price": s["price"],
            }
            friendly_slots.append(slot)

        self.status_attributes["slots"] = friendly_slots

        now = self.datetime(aware=True)
        slot = slots[0]

        # Try to find the next time charging will stop
        end_time = slot["start"]
        for s in slots:
            if s["start"] != end_time:
                break

            end_time = s["end"]

        self.status_attributes["next_start"] = (
            self.get_friendly_date(slot["start"])
            + " at "
            + slot["start"].strftime("%H:%M")
        )
        self.status_attributes["next_stop"] = (
            self.get_friendly_date(end_time)
            + " at "
            + end_time.strftime("%H:%M")
        )

        if slot["start"] < now < slot["end"]:
            self.start_charging()
            self.status_state = "charging"
        else:
            self.stop_charging()
            self.status_state = "stopped"

        self.status_attributes[
            "reason"
        ] = f"Price now {current_slot['price']:.2f}"
        self.update_status_entity()
        return True

    def convert_time_to_seconds(self, time_str):
        seconds = 0
        multipliers = [1, 60, 3600]

        for t in time_str.split(":"):
            seconds += int(t) * multipliers.pop()

        return seconds

    def get_config_value(self, param, default):
        if not param in self.args:
            return default

        v = self.get_entity_value(self.args[param])

        if isinstance(v, str):
            return v

        else:
            return self.args[param]

    def get_time_from_config(self, parameter, in_seconds=True):
        if not parameter in self.args:
            self.log(f"'{parameter}' not defined in config")
            return None

        time = self.get_state(self.args[parameter])
        if not time:
            time = self.args[parameter]

        return self.convert_time_to_seconds(time)

    def get_entity_and_attribute(self, name):
        s = name.split(",")

        try:
            if len(s) > 1:
                return s[0].strip(), s[1].strip()
            else:
                return s[0].strip(), None
        except Exception:
            return None, None

    def get_price(self):
        if "price_data" not in self.args:
            return None

        now = self.datetime(aware=True)
        midnight_today = self.datetime(aware=True).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # This is number of seconds from any midnight
        must_be_done_by = self.get_time_from_config("finish_at_latest_by")

        if (
            must_be_done_by is not None
            and (now - midnight_today).total_seconds() > must_be_done_by
        ):
            # add 24h to the must be done by (since we have passed the time
            # today already)
            must_be_done_by += 24 * 3600
            self.log(f"Required end time already passed. Adding 24 hours.")

        future_prices = []
        prices = []

        missing_price_info = False

        # merge all prices (today and tomorrow)
        for pd in self.args["price_data"]:
            e, a = self.get_entity_and_attribute(pd["entity"])
            part = self.get_state(entity_id=e, attribute=a)

            # Mark that we might miss required price info.
            # If we have price info up to the time when we must be finished,
            # then this is overridden
            if "required" in pd and pd["required"]:
                if not len(part):
                    missing_price_info = True
                    continue

                for p in part:
                    if "value" not in p or p["value"] is None:
                        missing_price_info = True
                        break

            prices += part

        # Sort prices based on start time
        prices = sorted(prices, key=lambda i: i["start"])

        for p in prices:
            if p["value"] is None:
                continue

            start = parser.parse(p["start"])
            end = parser.parse(p["end"])

            # If end time is in the past, skip it
            if (end - now).total_seconds() < 0:
                continue

            start_from_midnight_today = int(
                (start - midnight_today).total_seconds()
            )

            end_from_midnight_today = int(
                (end - midnight_today).total_seconds()
            )

            # If we have price info up to the point when we must
            # be finished with charging, we ignore that we miss
            # required price info.
            if (
                missing_price_info
                and must_be_done_by is not None
                and must_be_done_by > start_from_midnight_today
                and must_be_done_by <= end_from_midnight_today
            ):
                missing_price_info = False

            seconds_until_start = int((start - now).total_seconds())

            # If user say we bust have charged before a certain time
            # then don't include times later that that
            if (
                must_be_done_by is not None
                and start_from_midnight_today >= must_be_done_by
            ):
                # Start time is to late
                break

            # Calculate usable length based on time now and when we must be
            # done charging
            if (
                must_be_done_by is not None
                and end_from_midnight_today > must_be_done_by
            ):
                usable_length = must_be_done_by - start_from_midnight_today
            elif seconds_until_start < 0:
                usable_length = int((end - start).total_seconds())
                usable_length += seconds_until_start
            else:
                usable_length = int((end - start).total_seconds())

            # Store price info in our new parsed format
            future_prices.append(
                {
                    "start": start,
                    "end": end,
                    "start_from_midnight": start_from_midnight_today,
                    "end_from_midnight": end_from_midnight_today,
                    "length": usable_length,
                    "seconds_until_start": seconds_until_start,
                    "price": p["value"],
                }
            )

        if missing_price_info:
            self.debug("Missing required price info")

        return future_prices

    def update_status_entity(self):
        entity_id = "sensor." + self.name + "_status"

        self.debug(f"Updating status entity with: {self.status_state}")

        self.set_state(
            entity_id=entity_id,
            state=self.status_state,
            attributes=self.status_attributes,
        )
