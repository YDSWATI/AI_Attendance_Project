import os
import sqlite3
import smtplib
from email.mime.text import MIMEText
from typing import TypedDict, Literal

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors


# --------------------------------------------------
# GROQ MODEL
# --------------------------------------------------

def get_llm():
    return ChatGroq(
        api_key=os.getenv("GROQ_API_KEY"),
        model="llama-3.1-8b-instant",
        temperature=0
    )


# --------------------------------------------------
# CHAT MEMORY WITH SUMMARIZATION
# --------------------------------------------------

chat_history = {}
chat_summary = {}

# Holds a "pending action" per user while we wait for yes/no confirmation.
# This is what makes Human-in-the-Loop work without a LangGraph checkpointer.
pending_actions = {}


def get_history(user_id):
    if user_id not in chat_history:
        chat_history[user_id] = []
    return chat_history[user_id]


def get_summary(user_id):
    return chat_summary.get(user_id, "")


def maybe_summarize_history(user_id):
    history = get_history(user_id)
    if len(history) <= 10:
        return

    llm = get_llm()
    old_messages = history[:-4]
    history_text = "\n".join(
        f"{item['role']}: {item['content']}" for item in old_messages
    )

    prompt = f"""Summarize this conversation history in 3-4 sentences.
Focus on what the user asked and what data was found.

History:
{history_text}

Summary:"""

    result = llm.invoke([HumanMessage(content=prompt)])
    chat_summary[user_id] = result.content
    chat_history[user_id] = history[-4:]


# --------------------------------------------------
# DATABASE HELPERS
# --------------------------------------------------

def get_db_connection():
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "db.sqlite3"
    )
    return sqlite3.connect(db_path)


def tool_get_all_courses(user_id: int) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, name FROM attendance_course WHERE user_id=?",
            (user_id,)
        )
        courses = cursor.fetchall()
        if not courses:
            return "No courses found."
        return "COURSES:\n" + "\n".join(f"- [{cid}] {name}" for cid, name in courses)
    finally:
        conn.close()


def tool_get_course_students(user_id: int) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id FROM attendance_course WHERE user_id=?",
            (user_id,)
        )
        course_ids = [row[0] for row in cursor.fetchall()]
        if not course_ids:
            return "No courses found."

        placeholders = ",".join("?" * len(course_ids))
        cursor.execute(
            f"""
            SELECT s.name, s.roll_number, c.name
            FROM attendance_student s
            JOIN attendance_course c ON s.course_id = c.id
            WHERE s.course_id IN ({placeholders})
            """,
            course_ids
        )
        students = cursor.fetchall()
        if not students:
            return "No students found."
        return "STUDENTS:\n" + "\n".join(
            f"- {name} ({roll}) in {course}" for name, roll, course in students
        )
    finally:
        conn.close()


def tool_get_attendance_summary(user_id: int) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id FROM attendance_course WHERE user_id=?",
            (user_id,)
        )
        course_ids = [row[0] for row in cursor.fetchall()]
        if not course_ids:
            return "No courses found."

        placeholders = ",".join("?" * len(course_ids))
        cursor.execute(
            f"""
            SELECT s.name, COUNT(a.id), SUM(a.status)
            FROM attendance_attendance a
            JOIN attendance_student s ON s.id = a.student_id
            WHERE a.course_id IN ({placeholders})
            GROUP BY s.id
            """,
            course_ids
        )
        rows = cursor.fetchall()
        if not rows:
            return "No attendance records found."

        lines = ["ATTENDANCE SUMMARY:"]
        for name, total, present in rows:
            present = present or 0
            pct = round((present / total) * 100, 2) if total > 0 else 0
            lines.append(f"- {name}: {pct}% ({int(present)}/{total} classes)")
        return "\n".join(lines)
    finally:
        conn.close()


def get_low_attendance_rows(user_id: int, threshold: float = 75.0):
    """Returns raw rows (not a string) so email/pdf nodes can reuse this data."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id FROM attendance_course WHERE user_id=?",
            (user_id,)
        )
        course_ids = [row[0] for row in cursor.fetchall()]
        if not course_ids:
            return []

        placeholders = ",".join("?" * len(course_ids))
        cursor.execute(
            f"""
            SELECT s.name, s.roll_number, s.email, COUNT(a.id), SUM(a.status)
            FROM attendance_attendance a
            JOIN attendance_student s ON s.id = a.student_id
            WHERE a.course_id IN ({placeholders})
            GROUP BY s.id
            """,
            course_ids
        )
        rows = cursor.fetchall()

        results = []
        for name, roll, email, total, present in rows:
            present = present or 0
            pct = round((present / total) * 100, 2) if total > 0 else 0
            if pct < threshold:
                results.append({
                    "name": name,
                    "roll": roll,
                    "email": email,
                    "pct": pct,
                    "present": int(present),
                    "total": total
                })
        return results
    finally:
        conn.close()


def tool_get_low_attendance(user_id: int, threshold: float = 75.0) -> str:
    rows = get_low_attendance_rows(user_id, threshold)
    if not rows:
        return f"All students have attendance above {threshold}%."
    lines = [f"STUDENTS BELOW {threshold}%:"]
    for r in rows:
        lines.append(f"- {r['name']} ({r['roll']}): {r['pct']}%")
    return "\n".join(lines)


def find_student_by_name(user_id: int, name_query: str):
    """Looks up one student by (partial) name match, including email."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id FROM attendance_course WHERE user_id=?",
            (user_id,)
        )
        course_ids = [row[0] for row in cursor.fetchall()]
        if not course_ids:
            return None

        placeholders = ",".join("?" * len(course_ids))
        cursor.execute(
            f"""
            SELECT name, roll_number, email
            FROM attendance_student
            WHERE course_id IN ({placeholders}) AND name LIKE ?
            """,
            course_ids + [f"%{name_query}%"]
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {"name": row[0], "roll": row[1], "email": row[2]}
    finally:
        conn.close()


# --------------------------------------------------
# EMAIL TOOL
# --------------------------------------------------

def send_email(to_address: str, subject: str, body: str) -> bool:
    """Sends a plain-text email via Gmail SMTP using app password from .env """
    sender = os.getenv("EMAIL_ADDRESS")
    password = os.getenv("EMAIL_PASSWORD")

    if not sender or not password:
        raise ValueError("EMAIL_ADDRESS or EMAIL_PASSWORD not set in .env")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_address

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, [to_address], msg.as_string())

    return True


# --------------------------------------------------
# PDF REPORT TOOL
# --------------------------------------------------

def generate_attendance_pdf(user_id: int, output_dir: str = "media/reports") -> str:
    """Builds an attendance report PDF and returns the file path."""
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, f"attendance_report_user_{user_id}.pdf")

    rows = get_low_attendance_rows(user_id, threshold=1000)  # threshold=1000 -> get everyone

    doc = SimpleDocTemplate(file_path, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Attendance Report", styles["Title"]))
    story.append(Spacer(1, 16))

    if not rows:
        story.append(Paragraph("No attendance data available.", styles["Normal"]))
    else:
        table_data = [["Name", "Roll No.", "Present/Total", "Percentage"]]
        for r in rows:
            table_data.append([
                r["name"], r["roll"], f"{r['present']}/{r['total']}", f"{r['pct']}%"
            ])

        table = Table(table_data, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f4")]),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(table)

    doc.build(story)
    return file_path


# --------------------------------------------------
# LANGGRAPH STATE
# --------------------------------------------------

class AgentState(TypedDict):
    question: str
    response: str
    user_id: int
    intent: str            # courses | students | attendance | low_attendance | email | report | general
    db_context: str
    retry_count: int
    should_retry: bool
    needs_confirmation: bool
    pending_action: dict    # what we'll execute if user says "yes"


# --------------------------------------------------
# NODE 1: INTENT ROUTER
# --------------------------------------------------

def router_node(state: AgentState) -> AgentState:
    llm = get_llm()
    question = state["question"]

    prompt = f"""Classify this question into exactly one of these intents:
- courses        -> user asks about courses or subjects
- students       -> user asks about student list or details
- attendance     -> user asks about attendance percentages or records
- low_attendance -> user asks about students with low/poor/below attendance
- email          -> user wants to email/notify someone
- report         -> user wants a PDF/report/download
- general        -> anything else

Question: {question}

Reply with only the intent word, nothing else."""

    result = llm.invoke([HumanMessage(content=prompt)])
    intent = result.content.strip().lower()

    valid_intents = {
        "courses", "students", "attendance", "low_attendance",
        "email", "report", "general"
    }
    if intent not in valid_intents:
        intent = "general"

    state["intent"] = intent
    return state


# --------------------------------------------------
# NODE 2: TOOL CALLER (for read-only data intents)
# --------------------------------------------------

def tool_node(state: AgentState) -> AgentState:
    intent = state["intent"]
    user_id = state["user_id"]

    if intent == "courses":
        state["db_context"] = tool_get_all_courses(user_id)
    elif intent == "students":
        state["db_context"] = tool_get_course_students(user_id)
    elif intent == "attendance":
        state["db_context"] = tool_get_attendance_summary(user_id)
    elif intent == "low_attendance":
        state["db_context"] = tool_get_low_attendance(user_id, threshold=75.0)
    else:
        state["db_context"] = tool_get_all_courses(user_id)

    return state


# --------------------------------------------------
# NODE 3: ANSWER NODE (for normal Q&A intents)
# --------------------------------------------------

def answer_node(state: AgentState) -> AgentState:
    llm = get_llm()
    question = state["question"]
    user_id = state["user_id"]
    db_context = state["db_context"]

    summary = get_summary(user_id)
    history = get_history(user_id)
    history_text = "\n".join(
        f"{item['role']}: {item['content']}" for item in history[-6:]
    )

    prompt = f"""You are an AI Attendance Assistant.

Past Conversation Summary:
{summary if summary else "No prior summary."}

Recent Conversation:
{history_text if history_text else "No recent history."}

Relevant Data:
{db_context}

User Question:
{question}

Rules:
- Use only the data provided above.
- Never invent or assume records.
- Answer clearly and concisely.
- Format lists with dashes.
- If data is missing, say so honestly."""

    result = llm.invoke([HumanMessage(content=prompt)])
    state["response"] = result.content
    return state


# --------------------------------------------------
# NODE 4: CHECKER NODE (self-correction loop)
# --------------------------------------------------

def checker_node(state: AgentState) -> AgentState:
    response = state["response"]
    retry_count = state.get("retry_count", 0)

    if retry_count >= 2:
        state["should_retry"] = False
        return state

    bad_signals = [
        len(response.strip()) < 20,
        "i don't know" in response.lower() and state["intent"] != "general",
        "no data" in response.lower() and state["db_context"] not in ("", "No courses found."),
    ]

    if any(bad_signals):
        state["should_retry"] = True
        state["retry_count"] = retry_count + 1
    else:
        state["should_retry"] = False

    return state


# --------------------------------------------------
# NODE 5: EMAIL PREP NODE
# Figures out who to email + drafts the message, then asks for confirmation
# --------------------------------------------------

def email_prep_node(state: AgentState) -> AgentState:
    llm = get_llm()
    question = state["question"]
    user_id = state["user_id"]

    # Step 1: figure out target (named student, or "low attendance" group)
    prompt = f"""A user wants to send an email. Extract two things from their message:
1. WHO - either a specific student's name, or "low_attendance" if they mean
   all students below a certain attendance threshold, or "unknown" if unclear.
2. THRESHOLD - a number if mentioned (e.g. "below 75%"), else 75.

Message: {question}

Reply in this exact format:
WHO: <name or low_attendance or unknown>
THRESHOLD: <number>"""

    result = llm.invoke([HumanMessage(content=prompt)])
    text = result.content

    who = "unknown"
    threshold = 75.0
    for line in text.splitlines():
        if line.upper().startswith("WHO:"):
            who = line.split(":", 1)[1].strip()
        if line.upper().startswith("THRESHOLD:"):
            try:
                threshold = float(line.split(":", 1)[1].strip())
            except ValueError:
                threshold = 75.0

    # Step 2: resolve recipients
    recipients = []

    if who.lower() == "low_attendance":
        rows = get_low_attendance_rows(user_id, threshold)
        recipients = [r for r in rows if r.get("email")]
    elif who.lower() != "unknown":
        student = find_student_by_name(user_id, who)
        if student and student.get("email"):
            recipients = [student]

    if not recipients:
        state["response"] = (
            "I couldn't find a matching student with an email on file. "
            "Please check the name or make sure emails are added in the database."
        )
        state["needs_confirmation"] = False
        return state

    # Step 3: draft the email body
    names_preview = ", ".join(r["name"] for r in recipients[:5])
    draft_prompt = f"""
        Dear Student,

        Our records indicate that your attendance is below the required threshold of {threshold}%.

        Please attend upcoming classes regularly to improve your attendance record.

        Regards,
        Attendance Management System
        """.strip()

    draft = llm.invoke([HumanMessage(content=draft_prompt)]).content

    state["pending_action"] = {
        "type": "email",
        "recipients": recipients,
        "subject": "Attendance Notice",
        "body": draft
    }
    state["needs_confirmation"] = True
    state["response"] = (
        f"I'm about to email {len(recipients)} student(s): {names_preview}"
        f"{' and more' if len(recipients) > 5 else ''}.\n\n"
        f"Draft message:\n\"{draft}\"\n\n"
        f"Reply 'yes' to send, or 'no' to cancel."
    )
    return state


# --------------------------------------------------
# NODE 6: REPORT PREP NODE
# --------------------------------------------------

def report_prep_node(state: AgentState) -> AgentState:
    state["pending_action"] = {
        "type": "report",
        "user_id": state["user_id"]
    }
    state["needs_confirmation"] = True
    state["response"] = (
        "I can generate a PDF attendance report for all your students. "
        "Reply 'yes' to generate it, or 'no' to cancel."
    )
    return state


# --------------------------------------------------
# NODE 7: CONFIRMATION EXECUTOR
# Runs ONLY when the user's message is itself a yes/no to a pending action
# --------------------------------------------------

def execute_pending_action(user_id: int) -> str:
    action = pending_actions.get(user_id)
    if not action:
        return "There's nothing pending to confirm."

    if action["type"] == "email":
        sent = 0
        failed = []
        for r in action["recipients"]:
            try:
                send_email(r["email"], action["subject"], action["body"])
                sent += 1
            except Exception:
                failed.append(r["name"])

        msg = f"Sent {sent} email(s) successfully."
        if failed:
            msg += f" Failed for: {', '.join(failed)}."
        return msg

    if action["type"] == "report":
        try:
            path = generate_attendance_pdf(action["user_id"])
            return f"Report generated successfully. Saved at: {path}"
        except Exception as e:
            return f"Failed to generate report: {e}"

    return "Unknown pending action."


# --------------------------------------------------
# NODE 8: MEMORY NODE
# --------------------------------------------------

def memory_node(state: AgentState) -> AgentState:
    user_id = state["user_id"]
    history = get_history(user_id)
    history.append({"role": "user", "content": state["question"]})
    history.append({"role": "assistant", "content": state["response"]})
    maybe_summarize_history(user_id)
    return state


# --------------------------------------------------
# CONDITIONAL EDGES
# --------------------------------------------------

def route_after_intent(state: AgentState) -> Literal["tool", "email_prep", "report_prep"]:
    if state["intent"] == "email":
        return "email_prep"
    if state["intent"] == "report":
        return "report_prep"
    return "tool"


def should_retry(state: AgentState) -> Literal["answer", "end"]:
    if state.get("should_retry", False):
        return "answer"
    return "end"


# --------------------------------------------------
# BUILD THE GRAPH
# --------------------------------------------------

graph = StateGraph(AgentState)

graph.add_node("router", router_node)
graph.add_node("tool", tool_node)
graph.add_node("answer", answer_node)
graph.add_node("checker", checker_node)
graph.add_node("email_prep", email_prep_node)
graph.add_node("report_prep", report_prep_node)
graph.add_node("memory", memory_node)

graph.set_entry_point("router")

graph.add_conditional_edges(
    "router",
    route_after_intent,
    {
        "tool": "tool",
        "email_prep": "email_prep",
        "report_prep": "report_prep",
    }
)

graph.add_edge("tool", "answer")
graph.add_edge("answer", "checker")

graph.add_conditional_edges(
    "checker",
    should_retry,
    {
        "answer": "answer",
        "end": "memory"
    }
)

# Email/report prep nodes go straight to memory (they don't need the checker --
# their "answer" is a fixed confirmation message, not LLM-generated free text)
graph.add_edge("email_prep", "memory")
graph.add_edge("report_prep", "memory")

graph.add_edge("memory", END)

workflow = graph.compile()


# --------------------------------------------------
# MAIN FUNCTION
# --------------------------------------------------

CONFIRM_YES = {"yes", "y", "confirm", "send it", "go ahead", "do it"}
CONFIRM_NO = {"no", "n", "cancel", "stop"}


def run_agent(question: str, user_id: int) -> str:
    normalized = question.strip().lower()

    # --------------------------------------------------
    # HUMAN-IN-THE-LOOP CHECK
    # If there's a pending action waiting on this user, treat their message
    # as a yes/no instead of running it through the graph again.
    # --------------------------------------------------
    if user_id in pending_actions:
        if normalized in CONFIRM_YES:
            result_msg = execute_pending_action(user_id)
            del pending_actions[user_id]

            history = get_history(user_id)
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": result_msg})
            return result_msg

        if normalized in CONFIRM_NO:
            del pending_actions[user_id]
            msg = "Okay, cancelled. Nothing was sent or generated."

            history = get_history(user_id)
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": msg})
            return msg

        # If they typed something else instead of yes/no, drop the pending
        # action and process this as a brand new question.
        del pending_actions[user_id]

    # --------------------------------------------------
    # NORMAL GRAPH EXECUTION
    # --------------------------------------------------
    result = workflow.invoke({
        "question": question,
        "user_id": user_id,
        "response": "",
        "intent": "",
        "db_context": "",
        "retry_count": 0,
        "should_retry": False,
        "needs_confirmation": False,
        "pending_action": {},
    })

    # If this turn produced a pending action, store it for next message
    if result.get("needs_confirmation") and result.get("pending_action"):
        pending_actions[user_id] = result["pending_action"]

    response = result["response"]

    for symbol in ["**", "*", "###", "##", "#"]:
        response = response.replace(symbol, "")

    return response



















# import os
# import sqlite3
# from typing import TypedDict, Literal

# from langchain_groq import ChatGroq
# from langchain_core.messages import HumanMessage
# from langgraph.graph import StateGraph, END


# # --------------------------------------------------
# # GROQ MODEL
# # --------------------------------------------------

# def get_llm():
#     return ChatGroq(
#         api_key=os.getenv("GROQ_API_KEY"),
#         model="llama-3.1-8b-instant",
#         temperature=0
#     )


# # --------------------------------------------------
# # CHAT MEMORY WITH SUMMARIZATION
# # --------------------------------------------------

# chat_history = {}
# chat_summary = {}

# def get_history(user_id):
#     if user_id not in chat_history:
#         chat_history[user_id] = []
#     return chat_history[user_id]

# def get_summary(user_id):
#     return chat_summary.get(user_id, "")

# def maybe_summarize_history(user_id):
#     """If history is too long, summarize older messages to save context space."""
#     history = get_history(user_id)

#     if len(history) <= 10:
#         return  # Not long enough to summarize yet

#     llm = get_llm()
#     old_messages = history[:-4]  # Keep last 4 messages fresh

#     history_text = "\n".join(
#         f"{item['role']}: {item['content']}" for item in old_messages
#     )

#     prompt = f"""Summarize this conversation history in 3-4 sentences. 
# Focus on what the user asked and what data was found.

# History:
# {history_text}

# Summary:"""

#     result = llm.invoke([HumanMessage(content=prompt)])
#     chat_summary[user_id] = result.content

#     # Replace old history with only recent messages
#     chat_history[user_id] = history[-4:]


# # --------------------------------------------------
# # DATABASE TOOLS
# # --------------------------------------------------

# def get_db_connection():
#     db_path = os.path.join(
#         os.path.dirname(os.path.dirname(__file__)),
#         "db.sqlite3"
#     )
#     return sqlite3.connect(db_path)


# def tool_get_all_courses(user_id: int) -> str:
#     """Tool: Get all courses for a user."""
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     try:
#         cursor.execute(
#             "SELECT id, name FROM attendance_course WHERE user_id=?",
#             (user_id,)
#         )
#         courses = cursor.fetchall()
#         if not courses:
#             return "No courses found."
#         return "COURSES:\n" + "\n".join(f"- [{cid}] {name}" for cid, name in courses)
#     finally:
#         conn.close()


# def tool_get_course_students(user_id: int) -> str:
#     """Tool: Get all students across user's courses."""
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     try:
#         cursor.execute(
#             "SELECT id FROM attendance_course WHERE user_id=?",
#             (user_id,)
#         )
#         course_ids = [row[0] for row in cursor.fetchall()]
#         if not course_ids:
#             return "No courses found."

#         placeholders = ",".join("?" * len(course_ids))
#         cursor.execute(
#             f"""
#             SELECT s.name, s.roll_number, c.name
#             FROM attendance_student s
#             JOIN attendance_course c ON s.course_id = c.id
#             WHERE s.course_id IN ({placeholders})
#             """,
#             course_ids
#         )
#         students = cursor.fetchall()
#         if not students:
#             return "No students found."
#         return "STUDENTS:\n" + "\n".join(
#             f"- {name} ({roll}) in {course}" for name, roll, course in students
#         )
#     finally:
#         conn.close()


# def tool_get_attendance_summary(user_id: int) -> str:
#     """Tool: Get attendance percentage for all students."""
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     try:
#         cursor.execute(
#             "SELECT id FROM attendance_course WHERE user_id=?",
#             (user_id,)
#         )
#         course_ids = [row[0] for row in cursor.fetchall()]
#         if not course_ids:
#             return "No courses found."

#         placeholders = ",".join("?" * len(course_ids))
#         cursor.execute(
#             f"""
#             SELECT s.name, COUNT(a.id), SUM(a.status)
#             FROM attendance_attendance a
#             JOIN attendance_student s ON s.id = a.student_id
#             WHERE a.course_id IN ({placeholders})
#             GROUP BY s.id
#             """,
#             course_ids
#         )
#         rows = cursor.fetchall()
#         if not rows:
#             return "No attendance records found."

#         lines = ["ATTENDANCE SUMMARY:"]
#         for name, total, present in rows:
#             present = present or 0
#             pct = round((present / total) * 100, 2) if total > 0 else 0
#             lines.append(f"- {name}: {pct}% ({int(present)}/{total} classes)")
#         return "\n".join(lines)
#     finally:
#         conn.close()


# def tool_get_low_attendance(user_id: int, threshold: float = 75.0) -> str:
#     """Tool: Get students with attendance below a threshold."""
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     try:
#         cursor.execute(
#             "SELECT id FROM attendance_course WHERE user_id=?",
#             (user_id,)
#         )
#         course_ids = [row[0] for row in cursor.fetchall()]
#         if not course_ids:
#             return "No courses found."

#         placeholders = ",".join("?" * len(course_ids))
#         cursor.execute(
#             f"""
#             SELECT s.name, s.roll_number, COUNT(a.id), SUM(a.status)
#             FROM attendance_attendance a
#             JOIN attendance_student s ON s.id = a.student_id
#             WHERE a.course_id IN ({placeholders})
#             GROUP BY s.id
#             """,
#             course_ids
#         )
#         rows = cursor.fetchall()
#         if not rows:
#             return "No attendance records found."

#         low = []
#         for name, roll, total, present in rows:
#             present = present or 0
#             pct = round((present / total) * 100, 2) if total > 0 else 0
#             if pct < threshold:
#                 low.append(f"- {name} ({roll}): {pct}%")

#         if not low:
#             return f"All students have attendance above {threshold}%."
#         return f"STUDENTS BELOW {threshold}%:\n" + "\n".join(low)
#     finally:
#         conn.close()


# # --------------------------------------------------
# # LANGGRAPH STATE
# # --------------------------------------------------

# class AgentState(TypedDict):
#     question: str
#     response: str
#     user_id: int
#     intent: str          # "attendance" | "students" | "courses" | "low_attendance" | "general"
#     db_context: str      # data fetched by the right tool
#     retry_count: int     # how many times we've retried
#     should_retry: bool   # flag set by checker node


# # --------------------------------------------------
# # NODE 1: INTENT ROUTER
# # Classifies the question and picks which tool to use
# # --------------------------------------------------

# def router_node(state: AgentState) -> AgentState:
#     llm = get_llm()
#     question = state["question"]

#     prompt = f"""Classify this question into exactly one of these intents:
# - courses       → user asks about courses or subjects
# - students      → user asks about student list or details
# - attendance    → user asks about attendance percentages or records
# - low_attendance → user asks about students with low/poor/below attendance
# - general       → anything else

# Question: {question}

# Reply with only the intent word, nothing else."""

#     result = llm.invoke([HumanMessage(content=prompt)])
#     intent = result.content.strip().lower()

#     # Fallback if model returns something unexpected
#     valid_intents = {"courses", "students", "attendance", "low_attendance", "general"}
#     if intent not in valid_intents:
#         intent = "general"

#     state["intent"] = intent
#     return state


# # --------------------------------------------------
# # NODE 2: TOOL CALLER
# # Fetches only the data relevant to the intent
# # --------------------------------------------------

# def tool_node(state: AgentState) -> AgentState:
#     intent = state["intent"]
#     user_id = state["user_id"]

#     if intent == "courses":
#         state["db_context"] = tool_get_all_courses(user_id)

#     elif intent == "students":
#         state["db_context"] = tool_get_course_students(user_id)

#     elif intent == "attendance":
#         state["db_context"] = tool_get_attendance_summary(user_id)

#     elif intent == "low_attendance":
#         state["db_context"] = tool_get_low_attendance(user_id, threshold=75.0)

#     else:
#         # For general questions, give a light context
#         state["db_context"] = tool_get_all_courses(user_id)

#     return state


# # --------------------------------------------------
# # NODE 3: ANSWER NODE
# # Generates response using intent-specific data
# # --------------------------------------------------

# def answer_node(state: AgentState) -> AgentState:
#     llm = get_llm()
#     question = state["question"]
#     user_id = state["user_id"]
#     db_context = state["db_context"]

#     # Get memory (summary + recent history)
#     summary = get_summary(user_id)
#     history = get_history(user_id)
#     history_text = "\n".join(
#         f"{item['role']}: {item['content']}" for item in history[-6:]
#     )

#     prompt = f"""You are an AI Attendance Assistant.

# Past Conversation Summary:
# {summary if summary else "No prior summary."}

# Recent Conversation:
# {history_text if history_text else "No recent history."}

# Relevant Data:
# {db_context}

# User Question:
# {question}

# Rules:
# - Use only the data provided above.
# - Never invent or assume records.
# - Answer clearly and concisely.
# - Format lists with dashes.
# - If data is missing, say so honestly."""

#     result = llm.invoke([HumanMessage(content=prompt)])
#     state["response"] = result.content
#     return state


# # --------------------------------------------------
# # NODE 4: CHECKER NODE
# # Decides if the answer is good or needs retry
# # --------------------------------------------------

# def checker_node(state: AgentState) -> AgentState:
#     response = state["response"]
#     retry_count = state.get("retry_count", 0)

#     # Hard stop: don't retry more than 2 times
#     if retry_count >= 2:
#         state["should_retry"] = False
#         return state

#     # Simple checks for a bad answer
#     bad_signals = [
#         len(response.strip()) < 20,
#         "i don't know" in response.lower() and state["intent"] != "general",
#         "no data" in response.lower() and state["db_context"] not in ("", "No courses found."),
#     ]

#     if any(bad_signals):
#         state["should_retry"] = True
#         state["retry_count"] = retry_count + 1
#     else:
#         state["should_retry"] = False

#     return state


# # --------------------------------------------------
# # NODE 5: MEMORY NODE
# # Saves to history and summarizes if needed
# # --------------------------------------------------

# def memory_node(state: AgentState) -> AgentState:
#     user_id = state["user_id"]
#     history = get_history(user_id)

#     history.append({"role": "user", "content": state["question"]})
#     history.append({"role": "assistant", "content": state["response"]})

#     # Summarize if history is getting long
#     maybe_summarize_history(user_id)

#     return state


# # --------------------------------------------------
# # CONDITIONAL EDGE: retry or finish?
# # --------------------------------------------------

# def should_retry(state: AgentState) -> Literal["answer", "end"]:
#     if state.get("should_retry", False):
#         return "answer"   # loop back to answer node
#     return "end"


# # --------------------------------------------------
# # BUILD THE GRAPH
# # --------------------------------------------------

# graph = StateGraph(AgentState)

# graph.add_node("router", router_node)
# graph.add_node("tool", tool_node)
# graph.add_node("answer", answer_node)
# graph.add_node("checker", checker_node)
# graph.add_node("memory", memory_node)

# graph.set_entry_point("router")

# graph.add_edge("router", "tool")
# graph.add_edge("tool", "answer")
# graph.add_edge("answer", "checker")

# # Conditional: retry answer or move to memory
# graph.add_conditional_edges(
#     "checker",
#     should_retry,
#     {
#         "answer": "answer",   # retry
#         "end": "memory"       # done
#     }
# )

# graph.add_edge("memory", END)

# workflow = graph.compile()


# # --------------------------------------------------
# # MAIN FUNCTION
# # --------------------------------------------------

# def run_agent(question: str, user_id: int) -> str:
#     result = workflow.invoke({
#         "question": question,
#         "user_id": user_id,
#         "response": "",
#         "intent": "",
#         "db_context": "",
#         "retry_count": 0,
#         "should_retry": False,
#     })

#     response = result["response"]

#     # Clean markdown formatting
#     for symbol in ["**", "*", "###", "##", "#"]:
#         response = response.replace(symbol, "")

#     return response











# import os
# import sqlite3
# from typing import TypedDict

# from langchain_groq import ChatGroq
# from langchain_core.messages import HumanMessage
# from langgraph.graph import StateGraph, END


# # --------------------------------------------------
# # GROQ MODEL
# # --------------------------------------------------

# def get_llm():
#     return ChatGroq(
#         api_key=os.getenv("GROQ_API_KEY"),
#         model="llama-3.1-8b-instant",
#         temperature=0
#     )


# # --------------------------------------------------
# # SIMPLE CHAT MEMORY
# # --------------------------------------------------

# chat_history = {}

# def get_history(user_id):
#     if user_id not in chat_history:
#         chat_history[user_id] = []
#     return chat_history[user_id]


# # --------------------------------------------------
# # DATABASE CONTEXT (RAG)
# # --------------------------------------------------

# def get_db_context(user_id):

#     db_path = os.path.join(
#         os.path.dirname(os.path.dirname(__file__)),
#         "db.sqlite3"
#     )

#     conn = sqlite3.connect(db_path)
#     cursor = conn.cursor()

#     context = []

#     try:

#         cursor.execute(
#             """
#             SELECT id,name
#             FROM attendance_course
#             WHERE user_id=?
#             """,
#             (user_id,)
#         )

#         courses = cursor.fetchall()

#         if not courses:
#             return "No courses found."

#         context.append("COURSES:")

#         course_ids = []

#         for cid, cname in courses:
#             course_ids.append(cid)
#             context.append(f"- {cname}")

#         placeholders = ",".join("?" * len(course_ids))

#         cursor.execute(
#             f"""
#             SELECT
#                 s.name,
#                 s.roll_number,
#                 c.name
#             FROM attendance_student s
#             JOIN attendance_course c
#                 ON s.course_id=c.id
#             WHERE s.course_id IN ({placeholders})
#             """,
#             course_ids
#         )

#         students = cursor.fetchall()

#         context.append("\nSTUDENTS:")

#         for name, roll, course in students:
#             context.append(
#                 f"{name} ({roll}) - {course}"
#             )

#         cursor.execute(
#             f"""
#             SELECT
#                 s.name,
#                 COUNT(a.id),
#                 SUM(a.status)
#             FROM attendance_attendance a
#             JOIN attendance_student s
#                 ON s.id=a.student_id
#             WHERE a.course_id IN ({placeholders})
#             GROUP BY s.id
#             """,
#             course_ids
#         )

#         attendance = cursor.fetchall()

#         context.append("\nATTENDANCE:")

#         for name, total, present in attendance:

#             present = present or 0

#             pct = (
#                 round((present / total) * 100, 2)
#                 if total > 0 else 0
#             )

#             context.append(
#                 f"{name}: {pct}% attendance"
#             )

#     finally:
#         conn.close()

#     return "\n".join(context)


# # --------------------------------------------------
# # LANGGRAPH STATE
# # --------------------------------------------------

# class AgentState(TypedDict):
#     question: str
#     response: str
#     user_id: int


# # --------------------------------------------------
# # NODE
# # --------------------------------------------------

# def answer_question(state):
    

#     llm = get_llm()

#     question = state["question"]
#     user_id = state["user_id"]

#     db_context = get_db_context(user_id)

#     history = get_history(user_id)

#     history_text = ""

#     for item in history[-10:]:
#         history_text += f"{item['role']}: {item['content']}\n"

#     prompt = f"""
# You are an AI Attendance Assistant.

# Conversation History:
# {history_text}

# Database Data:
# {db_context}

# User Question:
# {question}

# Rules:
# - Use only available data.
# - Never invent records.
# - Answer clearly.
# - Format lists nicely.
# """

#     result = llm.invoke(
#         [HumanMessage(content=prompt)]
#     )

#     response = result.content

#     history.append({
#         "role": "user",
#         "content": question
#     })

#     history.append({
#         "role": "assistant",
#         "content": response
#     })

#     state["response"] = response

#     return state


# # --------------------------------------------------
# # LANGGRAPH
# # --------------------------------------------------

# graph = StateGraph(AgentState)

# graph.add_node(
#     "answer",
#     answer_question
# )

# graph.set_entry_point("answer")

# graph.add_edge(
#     "answer",
#     END
# )

# workflow = graph.compile()


# # --------------------------------------------------
# # MAIN FUNCTION
# # --------------------------------------------------

# def run_agent(question, user_id):

    result = workflow.invoke(
        {
            "question": question,
            "user_id": user_id,
            "response": ""
        }
    )

    response = result["response"]

    response = (
        response
        .replace("**", "")
        .replace("*", "")
        .replace("###", "")
        .replace("##", "")
        .replace("#", "")
    )

    return response