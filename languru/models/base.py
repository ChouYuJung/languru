from typing import Dict, List, Text, Type, TypeVar, Union

import pyjson5
from openai import OpenAI
from pyassorted.string import extract_code_blocks
from pydantic import BaseModel

from languru.prompts import PromptTemplate
from languru.prompts.repositories.data_model import prompt_date_model_from_openai

DataModelTypeVar = TypeVar("DataModelTypeVar", bound="DataModel")


class DataModel(BaseModel):
    @classmethod
    def models_from_openai(
        cls: Type[DataModelTypeVar],
        content: Text,
        client: "OpenAI",
        model: Text = "gpt-3.5-turbo",
        **kwargs,
    ) -> List[DataModelTypeVar]:
        # Get schema
        schema = cls.model_json_schema()
        model_schema = {cls.__name__: schema}
        # Prepare prompt
        prompt_template = PromptTemplate(prompt_date_model_from_openai)
        # Generate response
        chat_res = client.chat.completions.create(
            messages=[
                {"role": "system", "content": prompt_template.format()},
                {
                    "role": "user",
                    "content": (
                        f"[Model Schema]\n{model_schema}\n[END Model Schema]\n\n"
                        + f"{content}"
                    ),
                },
            ],
            model=model,
            temperature=0.0,
        )
        chat_answer = chat_res.choices[0].message.content
        if chat_answer is None:
            raise ValueError("Failed to generate a response from the OpenAI API.")
        # Parse response
        code_blocks = extract_code_blocks(chat_answer, language="json")
        if len(code_blocks) == 0:
            raise ValueError(
                f"Failed to extract a JSON code block from the response: {chat_answer}"
            )
        code_block = code_blocks[0]  # Only one code block is expected
        json_data: Union[Dict, List[Dict]] = pyjson5.loads(code_block)
        if isinstance(json_data, Dict):
            json_data = [json_data]
        return [cls.model_validate(item) for item in json_data]

    @classmethod
    def model_from_openai(
        cls: Type[DataModelTypeVar],
        content: Text,
        client: "OpenAI",
        model: Text = "gpt-3.5-turbo",
        **kwargs,
    ) -> DataModelTypeVar:
        models = cls.models_from_openai(content, client, model, **kwargs)  #
        if len(models) == 0:
            raise ValueError("Could not extract information from content.")
        return models[0]  # Only one model is expected
