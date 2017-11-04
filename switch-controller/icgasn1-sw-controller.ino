/**
 * @file
 * Test firmware for testing ICGASN1 
 */

// Includes

// Macro functions
#define _str(x) #x
#define str(x) _str(x)

// Defines
#define PIN_SW_AUTO 4
#define PIN_SW_XDIS A5
#define PIN_VBIAS A4
#define PIN_VSENS A2
#define PIN_XSW_OUT A0
#define PIN_LED 13 // on while discharging

#define SERIAL_BAUD 115200
#define SERIAL_CONFIG SERIAL_8N1

#define SAMPLE_MS 10
#define BTN_DEBOUNCE_MS 50

#define SENSE_AVG_SAMPLES 128 // powers of 2 only for efficiency - else division averaging is slow
#define SENSE_WINDOW_ADC ((uint16_t)(0.4*1024/5))
#define DISCHARGE_TIME_MS 100

// Types

typedef struct
{
    uint8_t  last_state;
    uint32_t debounce_expires;
} switch_t;

typedef struct
{
    uint16_t count;
    uint32_t target_sum;
    uint32_t sense_sum;
    uint32_t next_read_time;
} vmon_t;

// Global state

switch_t sw_auto = {LOW, 0};
switch_t sw_xdis = {LOW, 0};
vmon_t   vmonitor;
uint16_t manual_count = 0;
uint16_t seconds_tick = 0;

// Functions

void setup()
{
    pinMode(PIN_SW_AUTO, INPUT_PULLUP);
    pinMode(PIN_SW_XDIS, INPUT_PULLUP);
    pinMode(PIN_VBIAS, INPUT);
    pinMode(PIN_VSENS, INPUT);
    pinMode(PIN_XSW_OUT, OUTPUT);
    pinMode(PIN_LED, OUTPUT);
    
    memset(&vmonitor, 0, sizeof(vmon_t));
  
    Serial.begin(SERIAL_BAUD, SERIAL_CONFIG);
    while(!Serial)
    {
        // noop
    }
    Serial.println(F("ICGCASN1 Switch Controller"));
    Serial.println(F("SW_AUTO : " str(PIN_SW_AUTO) " [pullup]"));
    Serial.println(F("sw_xdis  : " str(PIN_SW_XDIS) " [pullup]"));
    Serial.println(F("VBIAS   : " str(PIN_VBIAS)));
    Serial.println(F("VSENS   : " str(PIN_VSENS)));
    Serial.println(F("~SW_OUT : " str(PIN_XSW_OUT)));
    Serial.println(F("LED     : " str(PIN_LED)));
    Serial.println(F("t, mod, sense, target, min, max"));
    set_discharge(false);
}

void loop()
{
    uint32_t cur_samp_start = millis();
    
    read_switch(&sw_auto, PIN_SW_AUTO);
    read_switch(&sw_xdis, PIN_SW_XDIS);
    
    if(sw_auto.last_state == HIGH)
    {
        read_adc(&vmonitor, PIN_VBIAS, PIN_VSENS);
        if(vmonitor.count == SENSE_AVG_SAMPLES)
        {
            vmonitor.count = 0;
            uint16_t target = vmonitor.target_sum / SENSE_AVG_SAMPLES;
            uint16_t sense = vmonitor.sense_sum / SENSE_AVG_SAMPLES;

            uint16_t target_max = target + SENSE_WINDOW_ADC;
            uint16_t target_min = (target > SENSE_WINDOW_ADC) ? target - SENSE_WINDOW_ADC : 0;

            Serial.print(seconds_tick++);

            // if out of range, discharge
            if(sense > target_max || sense < target_min)
            {
                Serial.print(",DIS");
                set_discharge(true);
                delay(DISCHARGE_TIME_MS);
                set_discharge(false);
            }
            else
            {
                Serial.print(",RUN");
            }

            Serial.print(",");
            Serial.print(sense);
            Serial.print(",");
            Serial.print(target);
            Serial.print(",");
            Serial.print(target_min);
            Serial.print(",");
            Serial.print(target_max);
            Serial.print("\n");
            memset(&vmonitor, 0, sizeof(vmon_t));
        }
    }
    else
    {
        set_discharge(sw_xdis.last_state == LOW);
        memset(&vmonitor, 0, sizeof(vmon_t));
        ++manual_count;
        if(manual_count == SENSE_AVG_SAMPLES)
        {
            manual_count = 0;
            Serial.print(seconds_tick++);
            Serial.print((sw_xdis.last_state == LOW) ? ",DIS" : ",RUN");
            Serial.print(",,,\n");
        }
    }

    // wait until next scheduled sample time
    uint32_t target_time = cur_samp_start + SAMPLE_MS;
    while(millis() < target_time);
}

void read_switch(switch_t * sw, uint8_t read_pin)
{
    // check debounce timeout expired
    if(millis() >= sw->debounce_expires)
        sw->debounce_expires = 0;

    // if debounce timeout expired, read switch
    if(sw->debounce_expires == 0)
    {
        // if switch state has changed, save state and set timeout
        uint8_t sw_new = digitalRead(read_pin);
        if(sw_new != sw->last_state)
        {
            sw->last_state = sw_new;
            sw->debounce_expires = millis() + BTN_DEBOUNCE_MS;
        }
    }
}

void read_adc(vmon_t * vm, uint8_t target_pin, uint8_t sense_pin)
{
    uint32_t now = millis();
    if(now >= vm->next_read_time)
    {
        vm->next_read_time = now + SAMPLE_MS;
        vm->target_sum += analogRead(target_pin);
        vm->sense_sum += analogRead(sense_pin);
        ++vm->count;
    }
}

void set_discharge(bool discharge)
{
    digitalWrite(PIN_XSW_OUT, discharge ? LOW : HIGH);
    digitalWrite(PIN_LED, discharge ? HIGH : LOW);
}

