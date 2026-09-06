"""One CRM care label plus all original offerings preserved in account notes."""
CARE_TYPES = {
    'Assisted Living': 'Assisted Living',
    'Memory Support': 'Memory Care',
    'Memory Care': 'Memory Care',
    'Short-Term Rehabilitation & Nursing': 'Skilled Nursing',
    'Skilled Nursing': 'Skilled Nursing',
    'Independent Living': 'Independent Living',
}
OPTIONS = ['Assisted Living', 'Memory Care', 'Skilled Nursing', 'Independent Living']

def suggested_care_type(offerings):
    mapped = {CARE_TYPES.get(offering) for offering in offerings}
    return next(iter(mapped)) if len(mapped) == 1 and None not in mapped else None

def choose_care_type(offerings, key):
    import streamlit as st
    suggested = suggested_care_type(offerings)
    if suggested:
        st.caption(f'CRM care type: {suggested}. All website offerings are retained in the note.')
        return suggested
    st.info('The CRM has one care_type field. Choose the primary label for this account; every original offering will remain in its note.')
    st.write('Website offerings:', offerings)
    selected = st.selectbox('Primary CRM care type', [None] + OPTIONS, format_func=lambda x: x or 'Select a reviewed mapping', key='care-' + key)
    if selected is None:
        st.stop()
    return selected
