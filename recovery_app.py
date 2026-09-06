import json
import streamlit as st
from decision_store import list_decisions
from recovery import ALLOWED, inspect_result, record_recovery

st.set_page_config(page_title='Verify uncertain CRM writes', layout='wide')
st.title('Verify uncertain CRM writes')
st.caption('Reads the CRM and records a recovery decision locally. Never sends a CRM update or creation.')
choices = [d for d in list_decisions() if d['status'] in ALLOWED]
if not choices:
    st.info('No uncertain single-account writes remain. Resume CHOW or duplicate operations in their dedicated apps if needed.')
    st.stop()
index = st.selectbox('Operation', range(len(choices)), format_func=lambda i: str(json.loads(choices[i]['proposal_json'])['operations'][0]))
decision = choices[index]
st.json(json.loads(decision['proposal_json']))
try:
    status, explanation, evidence = inspect_result(decision)
except Exception as error:
    st.error(str(error)); st.stop()
st.write(explanation)
st.json(evidence)
if status:
    confirmed = st.checkbox(f'Record the verified local status as {status}.')
    if st.button('Record verification', disabled=not confirmed):
        try:
            st.success(record_recovery(decision))
        except Exception as error:
            st.error(str(error))
