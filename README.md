Smart EV Charging
=================

_Charge your Electric Vehicle when the utility rate is the lowest_

Currently only Tesla is supported.

This AppDaemon app require a sensor to get the hourly rate. Currently the Nordpol Custom Component is supported. It also requires the Tesla Integration to work.

## Quick Example

This is an example configuration that will make sure charging is completed by 07:30 in the morning
```
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
    time_left: sensor.model_3_charging_rate_sensor,time_left
    start_by_the_latest_at: "03:00"
    debug: no
```

