#!/usr/bin/env python3
"""
XLI Self Healing v4 — Auto-retry, error analysis, new approach
"""

import asyncio
import random
from typing import Callable, Any, Optional, Dict
from dataclasses import dataclass

from xli.core.logger import StructuredLogger
from xli.providers.base import get_provider

logger = StructuredLogger("xli.heal")


@dataclass
class ErrorAnalysis:
    """Analyzed error information"""
    error_type: str
    severity: str  # low, medium, high, critical
    retryable: bool
    suggested_approach: str
    context: Dict[str, Any]


class SelfHealingEngine:
    """Auto-retry and error recovery"""
    
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.error_history: List[Dict] = []
    
    async def heal(self, task: str, error: Exception, 
                   attempt_func: Callable, context: str = "") -> Any:
        """Attempt to heal from error"""
        analysis = await self.analyze_error(error, context)
        
        logger.log_structured("INFO", "heal", 
                             f"Healing attempt: {analysis.error_type}", 
                             {"severity": analysis.severity, "retryable": analysis.retryable})
        
        if not analysis.retryable:
            logger.log_structured("WARN", "heal", "Error not retryable")
            raise error
        
        # Try with new approach
        for attempt in range(self.max_retries):
            delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
            
            logger.log_structured("DEBUG", "heal", 
                                 f"Retry {attempt + 1}/{self.max_retries}", 
                                 {"delay": delay})
            
            await asyncio.sleep(delay)
            
            try:
                # Modify approach based on analysis
                modified_task = f"{task}\n\n[PREVIOUS ERROR]: {str(error)}\n[APPROACH]: {analysis.suggested_approach}"
                result = await attempt_func(modified_task)
                
                logger.log_structured("INFO", "heal", 
                                     f"Healed on attempt {attempt + 1}")
                return result
                
            except Exception as e:
                logger.log_structured("WARN", "heal", 
                                     f"Attempt {attempt + 1} failed: {e}")
                self.error_history.append({
                    "attempt": attempt + 1,
                    "error": str(e),
                    "analysis": analysis
                })
        
        logger.log_structured("ERROR", "heal", "All healing attempts exhausted")
        raise error
    
    async def analyze_error(self, error: Exception, context: str = "") -> ErrorAnalysis:
        """Analyze error and classify"""
        error_str = str(error)
        error_type = type(error).__name__
        
        # Quick classification
        retryable_errors = [
            "Timeout", "Connection", "RateLimit", "Temporary",
            "ServiceUnavailable", "Network"
        ]
        fatal_errors = [
            "SyntaxError", "IndentationError", "ImportError",
            "ModuleNotFoundError", "TypeError", "ValueError"
        ]
        
        retryable = any(e in error_type for e in retryable_errors)
        fatal = any(e in error_type for e in fatal_errors)
        
        severity = "critical" if fatal else ("high" if not retryable else "medium")
        
        # Get LLM suggestion
        try:
            provider = get_provider()
            prompt = f"""Analyze this error and suggest a fix approach:

Error: {error_type}: {error_str}
Context: {context[:500]}

Return JSON:
{{
    "suggested_approach": "specific approach to fix",
    "retryable": true/false
}}"""
            
            response = await provider.chat([
                {"role": "system", "content": "Error analysis assistant"},
                {"role": "user", "content": prompt}
            ], temperature=0.3)
            
            # Parse JSON
            import json
            import re
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return ErrorAnalysis(
                    error_type=error_type,
                    severity=severity,
                    retryable=data.get("retryable", retryable),
                    suggested_approach=data.get("suggested_approach", "Try alternative approach"),
                    context={"original_error": error_str}
                )
                
        except Exception as e:
            logger.log_error("heal", "LLM analysis failed", exc=e)
        
        # Fallback
        return ErrorAnalysis(
            error_type=error_type,
            severity=severity,
            retryable=retryable,
            suggested_approach="Retry with modified parameters" if retryable else "Manual fix required",
            context={"original_error": error_str}
        )
    
    async def retry_with_backoff(self, func: Callable, *args, 
                                  max_retries: int = None, **kwargs) -> Any:
        """Retry function with exponential backoff"""
        max_retries = max_retries or self.max_retries
        
        for attempt in range(max_retries):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)
                    
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                
                delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                logger.log_structured("DEBUG", "heal", 
                                     f"Backoff retry {attempt + 1}, delay {delay:.1f}s")
                await asyncio.sleep(delay)


def get_healing_engine() -> SelfHealingEngine:
    """Get SelfHealingEngine instance"""
    return SelfHealingEngine()

