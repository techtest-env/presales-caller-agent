# DETAILED TEST CASE EXECUTION GUIDE

## Overview
4 comprehensive test cases to validate the voice agent system, including all languages and scenarios.

---

## TEST CASE 1: Successful Call with DB Push ✅

### Objective
Complete a full call where customer answers all 6 questions and data is pushed to PostgreSQL.

### Prerequisites
- Phone number that can receive calls (your personal phone)
- Agent is running and registered
- Database is accessible

### Execution Steps

**Step 1: Trigger the Call**
```bash
python main.py --userid test-success-001 --name "Rajesh" --phone "+919876543210"
```
(Replace with your actual phone number)

**Step 2: When Phone Rings**
- Pick up immediately
- Listen for: "Hi, am I speaking with Rajesh?"
- Respond: "Yes" or "Hi Rajesh here"

**Step 3: Agent Opens Conversation**
- Listen for: "Hi! I'm Priya from Relai. You were looking at properties in Hyderabad recently. Just have 6 quick questions to find your match. Is now a good time?"
- Respond: "Yes, sure" or "Now is good"

**Step 4: Answer 6 Questions ONE BY ONE**
```
Agent: "What type of property - Apartment or Villa?"
You: "Apartment"

Agent: "What's your budget range?"
You: "50 to 70 lakhs"

Agent: "Which areas in Hyderabad interest you?"
You: "Gachibowli, Banjara Hills"

Agent: "How many BHKs?"
You: "2 BHK"

Agent: "When do you need possession?"
You: "3 months"

Agent: "Best day and time for follow-up?"
You: "Tuesday 3 PM"
```

**Step 5: Verification**
After call ends, check:

a) **JSON File Created**
```bash
ls -la call_results/lead_test-success-001_*.json
```
Should show a file with timestamp.

b) **Check JSON Content**
```bash
cat call_results/lead_test-success-001_*.json | jq
```
Should show all 6 answers:
```json
{
  "lead_id": "test-success-001",
  "name": "Rajesh",
  "phone_number": "+919876543210",
  "call_time": "2026-05-13T...",
  "answers": {
    "property_type": "Apartment",
    "budget": "50 to 70 lakhs",
    "areas": "Gachibowli, Banjara Hills",
    "bhk": "2 BHK",
    "possession_timeline": "3 months",
    "follow_up_time": "Tuesday 3 PM",
    "additional_notes": ""
  }
}
```

c) **Check PostgreSQL**
```sql
-- Connect to Supabase/PostgreSQL
SELECT * FROM client_Requirements 
WHERE lead_id = 'test-success-001' 
ORDER BY id DESC LIMIT 1;
```
Should show record with all preferences populated.

### Expected Result
- ✅ Call completes naturally
- ✅ Agent asks all 6 questions
- ✅ JSON file saved with all answers
- ✅ PostgreSQL record created with ID populated

### Logs to Check
```
[AGENT LOG] INFO:outbound-agent:Speaking greeting
[AGENT LOG] INFO:outbound-agent:Agent is now in conversation mode
[AGENT LOG] INFO:outbound-agent:Saved call details to call_results/...
[AGENT LOG] INFO:outbound-agent:Successfully pushed call details to DB with record ID [X]
```

---

## TEST CASE 2: Unsuccessful Call (User Hangs Up) ❌

### Objective
Simulate user hanging up early or saying "not interested" - ensure graceful shutdown and partial data save.

### Execution Steps

**Option A: User Says "Not Interested"**
```bash
python main.py --userid test-failed-001 --name "Deepak" --phone "+919876543210"
```

When agent speaks:
```
Agent: "Hi! I'm Priya... Is now a good time?"
You: "No, I'm not interested" or "Not right now"
```

**Option B: User Hangs Up**
```bash
python main.py --userid test-failed-002 --name "Priya" --phone "+919876543210"
```

When agent speaks:
- Pick up
- Listen to greeting
- **HANG UP IMMEDIATELY** (or wait 3+ seconds without responding)

### Verification

**Check JSON File**
```bash
cat call_results/lead_test-failed-001_*.json | jq .answers
```
Should show:
```json
{
  "property_type": "",
  "budget": "",
  "areas": "",
  "bhk": "",
  "possession_timeline": "",
  "follow_up_time": "",
  "additional_notes": "Call ended abruptly by user hangup"
}
```

**Check PostgreSQL**
```sql
SELECT lead_id, lead_name, lr_notes FROM client_Requirements 
WHERE lead_id IN ('test-failed-001', 'test-failed-002')
ORDER BY id DESC LIMIT 2;
```
Should show records with empty preferences and notes about early termination.

### Expected Result
- ✅ No crashes in agent logs
- ✅ JSON file created with empty/partial data
- ✅ DB record created with empty preferences
- ✅ Clean shutdown message in logs
- ✅ No database errors

### Logs to Check
```
[AGENT LOG] ERROR (if any): Should NOT have critical errors
[AGENT LOG] INFO:outbound-agent:Room disconnected
[AGENT LOG] INFO:outbound-agent:Saved call details to call_results/
[AGENT LOG] INFO:outbound-agent:Successfully pushed call details to DB
```

---

## TEST CASE 3: Half Successful (Partial Info + Follow-up) 🟡

### Objective
Customer provides partial information (2-4 questions) and schedules a follow-up call.

### Execution Steps

**Trigger Call**
```bash
python main.py --userid test-partial-001 --name "Sita" --phone "+919876543210"
```

**Response Pattern**
```
Agent: "Hi, am I speaking with Sita?"
You: "Yes"

Agent: "Is now a good time?"
You: "Yes, but just quickly"

Agent: "What type of property?"
You: "Apartment"

Agent: "Budget?"
You: "Let me think... maybe 40 lakhs"

Agent: "Which areas?"
You: "Umm, I'm not sure actually. Can you call me back later?"

Agent: "Sure! What's the best day and time?"
You: "Call me Thursday evening, around 5 PM"

Agent: [calls end_call] ✅
```

### Verification

**Check JSON**
```bash
cat call_results/lead_test-partial-001_*.json | jq .answers
```
Should show:
```json
{
  "property_type": "Apartment",
  "budget": "40 lakhs",
  "areas": "",
  "bhk": "",
  "possession_timeline": "",
  "follow_up_time": "Thursday evening 5 PM",
  "additional_notes": ""
}
```

**Check Database**
```sql
SELECT lead_id, lead_name, preferences->>'follow_up_time' as follow_up
FROM client_Requirements
WHERE lead_id = 'test-partial-001';
```
Should show:
```
lead_id: test-partial-001
follow_up_time: Thursday evening 5 PM
```

### Expected Result
- ✅ Agent collects partial data gracefully
- ✅ Respects when customer wants callback
- ✅ Follow-up time is properly captured
- ✅ JSON saved with partially filled answers
- ✅ DB record marks follow-up scheduled

### Logs to Check
```
[AGENT LOG] INFO: Saved call details
[AGENT LOG] INFO: Successfully pushed... with record ID
```

---

## TEST CASE 4: Language Mixing (Multilingual) 🌍 **CORE FEATURE**

### Objective
Demonstrate seamless language switching between English, Telugu, and Hindi (Hinglish).

### This is the MOST IMPORTANT Test Case - Your Competitive Advantage!

### Execution Steps

**Trigger Call**
```bash
python main.py --userid test-multilang-001 --name "Hari" --phone "+919876543210"
```

**Conversation with Language Switches**
```
Agent (English): "Hi, am I speaking with Hari?"
You (English): "Yes"

Agent (English): "Is now a good time?"
You (Hinglish/Telugu Mix): "Haa, lekin main Telugu bolna chahta hoon"
   Translation: "Yes, but I want to speak in Telugu"

Agent (Switches to Telugu - Phonetic): "Bilkul! Nenu Telugu lo matladagalanu. 
   Em type property kavali? Apartment ya Villa?"
   Translation: "Of course! I can speak in Telugu. What property type do you want?"

You (Telugu/Hinglish): "Apartment, lekin budget mera 50 lakh tak hai"
   Translation: "Apartment, but my budget is up to 50 lakhs"

Agent (Telugu/English Mix): "Sari ga. Budget 50 lakh. Em areas lo kavali?"
   Translation: "OK, 50 lakh budget. Which areas do you want?"

You (English): "Gachibowli and Kondapur"

Agent (Telugu): "Excellent. Gachibowli, Kondapur. Enta BHK kavali?"
   Translation: "How many BHKs?"

You (Hinglish): "3 BHK chahiye mujhe"
   Translation: "I need 3 BHK"

... [Continue answering remaining questions in mixed language] ...

Agent: [Summarizes in Telugu] "Sari ga. Apartment, 50 lakh, Gachibowli-Kondapur, 
   3 BHK. Follow-up ke liye Thursday?"
   Translation: "OK, Apartment, 50 lakhs, Gachibowli-Kondapur, 3 BHK. Follow-up Thursday?"

You: "Yes, Thursday 2 PM"

Agent: [calls end_call]
```

### Verification

**Check JSON**
```bash
cat call_results/lead_test-multilang-001_*.json | jq .answers
```
Should show all answers collected despite language switching:
```json
{
  "property_type": "Apartment",
  "budget": "50 lakh",
  "areas": "Gachibowli, Kondapur",
  "bhk": "3 BHK",
  "possession_timeline": "...",
  "follow_up_time": "Thursday 2 PM",
  "additional_notes": "Conversation conducted in Telugu/Hinglish mix"
}
```

**Check Agent Logs**
```bash
tail -50 agent.log | grep -i "telugu\|hindi\|language"
```
Should show language detection/switching logs.

**Check Database**
```sql
SELECT lead_id, lead_name, preferences 
FROM client_Requirements
WHERE lead_id = 'test-multilang-001';
```
All data should be captured correctly despite language switching.

### Expected Result - CRITICAL SUCCESS CRITERIA
- ✅ Agent detects customer wants Telugu mid-call
- ✅ Agent switches language seamlessly
- ✅ Agent continues asking questions in Telugu
- ✅ Agent handles Hinglish/mixed language input
- ✅ Agent maintains conversation context across languages
- ✅ All data captured correctly regardless of language used
- ✅ No crashes or language-related errors
- ✅ Tone remains natural and professional

### Logs to Check
```
[AGENT LOG] INFO: Language detection working
[AGENT LOG] INFO: Speaking in Telugu
[AGENT LOG] INFO: Handling multilingual input
```

### Why This Test Case is Critical
🎯 This demonstrates your system's ability to serve the Indian market where:
- Customers switch between English, Hindi, Telugu daily
- Many speak in Hinglish (Hindi + English mix)
- Strict language preference enforcement is ESSENTIAL

---

## RUNNING ALL TESTS AUTOMATICALLY

### Create a Test Runner Script
```bash
# test_runner.sh
#!/bin/bash

echo "Running all 4 test cases..."

echo "\n=== TEST CASE 1: Successful Call ==="
python main.py --userid test-success-001 --name "Rajesh" --phone "+919876543210"

echo "\n=== TEST CASE 2: Failed Call ==="
python main.py --userid test-failed-001 --name "Deepak" --phone "+919876543210"

echo "\n=== TEST CASE 3: Partial Call ==="
python main.py --userid test-partial-001 --name "Sita" --phone "+919876543210"

echo "\n=== TEST CASE 4: Multilingual ==="
python main.py --userid test-multilang-001 --name "Hari" --phone "+919876543210"

echo "\n=== VERIFICATION ==="
echo "Checking call results..."
ls -la call_results/lead_*

echo "\nChecking database records..."
psql -c "SELECT COUNT(*) FROM client_Requirements WHERE lead_id LIKE 'test-%';"
```

---

## SUCCESS CHECKLIST

### Test Case 1: Successful ✅
- [ ] All 6 questions answered
- [ ] JSON file has all data
- [ ] DB record created
- [ ] No errors in logs

### Test Case 2: Failed ✅
- [ ] Graceful shutdown
- [ ] JSON created with empty fields
- [ ] DB record created
- [ ] No critical errors

### Test Case 3: Partial ✅
- [ ] Partial data saved
- [ ] Follow-up time captured
- [ ] DB record reflects partial state
- [ ] Natural handoff to follow-up

### Test Case 4: Multilingual ✅
- [ ] Language detection works
- [ ] Switches language properly
- [ ] Maintains context across switches
- [ ] All data captured correctly
- [ ] No language-related crashes

---

## TROUBLESHOOTING

### Agent Not Speaking After Greeting
**Check:**
- Agent logs for errors
- LLM provider (Groq) is responding
- System prompt is loading correctly
**Solution:** Restart agent, check API keys

### Database Insert Failing
**Check:**
- DATABASE_URL in .env
- PostgreSQL connection is active
- Schema matches expected fields
**Solution:** Manually test DB connection

### Language Mixing Not Working
**Check:**
- Deepgram STT recognizing non-English
- LLM understanding multilingual input
- TTS can output in that language
**Solution:** Check language settings in config.py

---

## DEMO FLOW FOR STAKEHOLDERS

Use this exact order to showcase the system:

1. **Test Case 1** → "Look! Complete successful call with data in DB"
2. **Test Case 4** → "Look! Our agent speaks Telugu, Hindi, and English seamlessly - this is our competitive advantage"
3. **Test Case 3** → "Look! Handles partial calls and schedules follow-ups automatically"
4. **Test Case 2** → "Look! Gracefully handles rejections without crashing"

This order shows maturity, language capabilities, and robustness.
