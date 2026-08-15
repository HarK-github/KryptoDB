# KryptoDB Demo Scenario: Insider Threat Investigation

This document contains a sequential list of prompts to run in an MCP-enabled agent (like Claude Desktop or Cursor). This scenario builds a complex, interconnected graph across multiple encrypted scopes and demonstrates KryptoDB's ability to maintain strict access boundaries while linking complex relationships.

---

## 🎬 Prerequisites
Before starting, ensure your Agent is configured with an API key that has access to the following scopes:
- `ORG:acme-structure`
- `PROJECT:alpha`
- `RESEARCH:threat-intel`

---

## 🏗️ Phase 1: Establishing the Organization (Scope: ORG:acme-structure)
*Goal: Build the foundational nodes for the company structure.*

**Prompt 1:**
 "Activate the `ORG:acme-structure` partition. Once active, store the following facts about our organization in the `ORG:acme-structure` scope:
 1. 'Alice' works for 'Acme Corp'
 2. 'Bob' works for 'Acme Corp'
 3. 'Alice' manages 'Bob'
 4. 'Alice' is assigned to 'Engineering Department'
 5. 'Bob' is assigned to 'Engineering Department'
 
 Confirm once these are stored."

---

## 🚀 Phase 2: Storing Sensitive Project Data (Scope: PROJECT:alpha)
*Goal: Create a separate, isolated scope for a top-secret project and link it to the employees.*

**Prompt 2:**
 "Now, activate the `PROJECT:alpha` partition. Store the following technical architecture facts in the `PROJECT:alpha` scope:
 1. 'Project Alpha' relies on 'Database Cluster X'
 2. 'Database Cluster X' contains 'Customer PII'
 3. 'Bob' has admin access to 'Database Cluster X'
 4. 'Alice' is the owner of 'Project Alpha'
 
 Let me know when this is complete."

---

## 🚨 Phase 3: Logging Threat Intelligence (Scope: RESEARCH:threat-intel)
*Goal: Introduce a third scope that connects the dots between a threat actor and our internal infrastructure.*

**Prompt 3:**
 "Activate the `RESEARCH:threat-intel` partition. We received some concerning logs. Store these facts in the `RESEARCH:threat-intel` scope:
 1. 'Suspicious IP 192.168.1.50' attacked 'Database Cluster X'
 2. 'Suspicious IP 192.168.1.50' is associated with 'Bob'
 3. 'Data Exfiltration Alert' triggered on 'Database Cluster X'
 
 Verify that the facts were successfully proposed."

---

## 🧠 Phase 4: Complex Multi-Scope Retrieval
*Goal: Prove that the agent can traverse the graph across different encrypted partitions to answer complex questions.*

**Prompt 4:**
 "Ensure that `ORG:acme-structure`, `PROJECT:alpha`, and `RESEARCH:threat-intel` are all active in your session. 
 
 Using your memory query tools, investigate 'Database Cluster X'. Traverse the connections to tell me:
 1. Who has admin access to the cluster?
 2. Who is that person's manager?
 3. Why might this person be a security risk based on our threat intel?
 
 Please synthesize the story of what happened based on the graph."

---

## 🔒 Phase 5: Zero-Trust & Access Revocation (Optional)
*Goal: Show what happens when access is denied.*

**Prompt 5 (Run this in a NEW chat window or agent with restricted keys):**
 "I need to know who has admin access to 'Database Cluster X'. Query the memory to find out."
 *(Expected Result: The agent will fail to retrieve the data because the `PROJECT:alpha` scope is encrypted, and without activating the partition with the correct API key, the graph relationships remain cryptographically masked.)*
