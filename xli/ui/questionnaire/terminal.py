#!/usr/bin/env python3
"""
XLI Terminal Questionnaire — input() based
"""

from typing import Dict, List

from xli.ui.questionnaire.base import Question


class TerminalQuestionnaire:
    """Terminal-based questionnaire"""
    
    async def run(self, questions: List[Question]) -> Dict[str, str]:
        """Run questionnaire in terminal"""
        print("\n" + "=" * 60)
        print(" 🔥 XLI PRO — Task Clarification ")
        print("=" * 60)
        
        answers = {}
        
        for q in questions:
            print(f"\n❓ {q.question}")
            
            if q.options:
                for i, opt in enumerate(q.options, 1):
                    print(f"   {i}. {opt}")
                
                while True:
                    try:
                        choice = input("   Select: ").strip()
                        if not choice and q.default:
                            answers[q.id] = q.default
                            break
                        
                        idx = int(choice) - 1
                        if 0 <= idx < len(q.options):
                            answers[q.id] = q.options[idx]
                            break
                        
                        print("   ❌ Invalid choice")
                        
                    except ValueError:
                        if not q.required and not choice:
                            break
                        print("   Enter a number")
            else:
                default_hint = f" [{q.default}]" if q.default else ""
                answer = input(f"   Answer{default_hint}: ").strip()
                
                if not answer and q.default:
                    answer = q.default
                
                answers[q.id] = answer
        
        print("\n✅ Clarification complete!")
        print("=" * 60)
        
        return answers

